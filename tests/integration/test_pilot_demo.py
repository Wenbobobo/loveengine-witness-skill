from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveengine_witness.pilot_demo import run_pilot_demo
from loveengine_witness.core_transcript import verify_core_transcript
from loveengine_witness.pilot_transcript import verify_pilot_transcript


@pytest.mark.integration
def test_witness_core_pilot_stops_at_gate(tmp_path: Path) -> None:
    result = run_pilot_demo(
        tmp_path,
        run_id="core-pilot-test-001",
        event_count=3,
        observer_count=3,
        simulate_faults=False,
    )
    transcript = result["transcript"]

    assert result["stage"] == "core"
    assert result["package_installed"] is True
    assert result["observation_receipts"] == 3
    assert result["review_receipts"] == 3
    assert result["gate_ready"] is True
    assert result["snapshot_restore_verified"] is True
    assert result["environment"] == "local_anvil"
    assert result["actors_simulated"] is True
    assert result["chain_verification"] == "chain_consistency"
    assert transcript["run_id"] == "core-pilot-test-001"
    assert "proposal" not in transcript
    assert "vote_approvals" not in transcript
    assert "final_state" not in transcript
    assert verify_core_transcript(transcript)["valid"] is True
    audit_records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    task_ingress = [
        item
        for item in audit_records
        if item.get("path") == "/v1/relay/tasks" and item.get("status") == 202
    ]
    assert len(task_ingress) == 6


@pytest.mark.integration
def test_witness_core_pilot_replays_delayed_observer_after_fault(tmp_path: Path) -> None:
    event_count = 3
    result = run_pilot_demo(
        tmp_path,
        run_id="core-pilot-delayed-observer-001",
        event_count=event_count,
        observer_count=3,
        simulate_faults=True,
    )
    transcript = result["transcript"]
    delayed_task_id = "observe:lan-pilot-session-001:3"
    delayed_receipt = next(
        receipt
        for receipt in transcript["observation_receipts"]
        if receipt["task_id"] == delayed_task_id
    )

    assert result["stage"] == "core"
    assert result["gate_ready"] is True
    assert result["faults"]["agent_disconnects"] == 3
    proofs = result["faults"]["agent_disconnect_proofs"]
    assert len(proofs) == 3
    assert {proof["task_id"] for proof in proofs} == {
        f"observe:lan-pilot-session-001:{index}" for index in range(1, 4)
    }
    assert all(
        proof["accepted"] is True and proof["connection_closed"] is True
        for proof in proofs
    )
    receipt_ack_loss = result["faults"]["receipt_ack_loss_proofs"]
    assert len(receipt_ack_loss) == 3
    assert all(
        proof["receipt_stored"] is True
        and proof["confirmation_ack_dropped"] is True
        and proof["receipt_state_recovered"] is True
        and proof["receipt_confirmed"] is True
        for proof in receipt_ack_loss
    )
    assert len(transcript["observation_receipts"]) == 3
    assert delayed_receipt["status"] == "completed"
    assert delayed_receipt["result"]["observed_from"] == "0"
    assert delayed_receipt["result"]["event_count"] == str(event_count)
    assert delayed_receipt["result"]["last_sequence"] == str(event_count)
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
