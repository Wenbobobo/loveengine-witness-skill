from __future__ import annotations

import copy

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_transcript import (
    pilot_transcript_hash,
    verify_pilot_transcript,
)


def _fixture() -> dict:
    value = {
        "schema_version": "loveengine.pilot-transcript/1",
        "run_id": "pilot-1",
        "version": "0.5.0-lan-pilot",
        "package": {"archive_keccak256": "0x" + "11" * 32},
        "chain": {
            "chain_id": "31337",
            "registry": "0x" + "12" * 20,
            "witness_dao": "0x" + "13" * 20,
            "public_sink": "0x" + "14" * 20,
        },
        "session": {
            "session_id": "s1",
            "status": "closed",
            "head_event_hash": "0x" + "21" * 32,
        },
        "events": [],
        "observation_set": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "evidence_bundle": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "dispute": {"status": "dismissed"},
        "proposal_gate": {"ready": True},
        "proposal": {
            "proposal_id": "1",
            "payload_hash": "0x" + "31" * 32,
        },
        "vote_approvals": [
            {"proposal_id": "1", "payload_hash": "0x" + "31" * 32}
            for _ in range(5)
        ],
        "transactions": [],
        "final_state": {"proposal_executed": True, "total_uto": "20"},
        "metrics": {},
        "snapshots": [],
    }
    value["transcript_hash"] = pilot_transcript_hash(value)
    return value


def test_pilot_transcript_cross_checks_every_phase() -> None:
    value = _fixture()
    assert verify_pilot_transcript(value)["valid"] is True

    tampered = copy.deepcopy(value)
    tampered["vote_approvals"][0]["payload_hash"] = "0x" + "ff" * 32
    tampered["transcript_hash"] = pilot_transcript_hash(tampered)
    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(tampered)
    assert exc.value.code == "pilot_cross_reference_mismatch"
