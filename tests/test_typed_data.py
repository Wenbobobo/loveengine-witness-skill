from __future__ import annotations

from loveengine_witness.typed_data import build_register_typed_data, build_vote_typed_data


DAO = "0x" + "1" * 40
WITNESS = "0x" + "2" * 40


def test_register_typed_data_binds_domain_nonce_and_deadline() -> None:
    value = build_register_typed_data(
        {
            "chain_id": "31337",
            "verifying_contract": DAO,
            "witness": WITNESS,
            "nonce": "0",
            "deadline": "1000",
        }
    )

    assert value["primaryType"] == "Register"
    assert value["domain"]["chainId"] == 31337
    assert value["domain"]["verifyingContract"] == DAO
    assert value["message"] == {
        "witness": WITNESS,
        "nonce": 0,
        "deadline": 1000,
    }


def test_vote_typed_data_binds_proposal_and_payload() -> None:
    value = build_vote_typed_data(
        {
            "chain_id": "31337",
            "verifying_contract": DAO,
            "witness": WITNESS,
            "proposal_id": "7",
            "support": False,
            "reason_hash": "0x" + "3" * 64,
            "payload_hash": "0x" + "4" * 64,
            "nonce": "2",
            "deadline": "1000",
        }
    )

    assert value["primaryType"] == "Vote"
    assert value["message"]["proposalId"] == 7
    assert value["message"]["support"] is False
    assert value["message"]["payloadHash"] == "0x" + "4" * 64
