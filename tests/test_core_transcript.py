from __future__ import annotations

import copy

import pytest

import loveengine_witness.core_transcript as core_transcript_module
from loveengine_witness.core_transcript import (
    core_transcript_hash,
    verify_core_transcript,
)
from loveengine_witness.errors import LoveEngineError
from test_pilot_transcript import _trust_policy, _v2_fixture


def _core_fixture() -> dict:
    pilot = _v2_fixture()
    value = {
        key: copy.deepcopy(item)
        for key, item in pilot.items()
        if key
        not in {
            "schema_version",
            "chain",
            "proposal",
            "vote_approvals",
            "transaction_receipts",
            "contract_code_hashes",
            "final_state",
            "snapshots",
            "transcript_hash",
        }
    }
    value["schema_version"] = "loveengine.witness-core-transcript/1"
    value["chain"] = {
        "chain_id": pilot["chain"]["chain_id"],
        "registry": pilot["chain"]["registry"],
        "publisher": pilot["chain"]["publisher"],
    }
    value["transcript_hash"] = core_transcript_hash(value)
    return value


def test_core_transcript_stops_at_verified_gate() -> None:
    value = _core_fixture()

    result = verify_core_transcript(value)

    assert result["valid"] is True
    assert result["verification_level"] == "offline_integrity"
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False
    assert result["gate_ready"] is True
    assert "proposal" not in value
    assert "vote_approvals" not in value
    assert "final_state" not in value


def test_core_transcript_rpc_requires_policy_for_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _core_fixture()
    monkeypatch.setattr(
        core_transcript_module,
        "verify_release_anchor_rpc",
        lambda transcript, rpc_url: (object(), 10),
    )

    consistency = verify_core_transcript(value, rpc_url="http://rpc.invalid")
    trusted = verify_core_transcript(
        value,
        rpc_url="http://rpc.invalid",
        trust_policy=_trust_policy(value),
    )

    assert consistency["verification_level"] == "chain_consistency"
    assert consistency["trust_bound"] is False
    assert trusted["verification_level"] == "chain_verified"
    assert trusted["trust_bound"] is True


def test_core_transcript_rejects_stage_tampering() -> None:
    value = _core_fixture()
    value["artifacts"][0]["size"] = 999
    value["transcript_hash"] = core_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_core_transcript(value)

    assert exc.value.code == "pilot_cross_reference_mismatch"


def test_core_schema_rejects_governance_state() -> None:
    value = _core_fixture()
    value["vote_approvals"] = []
    value["transcript_hash"] = core_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_core_transcript(value)

    assert exc.value.code == "schema_validation_failed"
