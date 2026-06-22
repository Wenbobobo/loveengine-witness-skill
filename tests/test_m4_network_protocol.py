from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import build_task_v2, verify_task_v2
from loveengine_witness.m4_typed_data import build_task_v2_typed_data


REGISTRY = "0x00000000000000000000000000000000000000aa"


def test_v2_task_uses_domain_version_two_and_detects_tampering() -> None:
    issuer = Account.create()
    recipient = Account.create()
    task = build_task_v2(
        chain_id="31337",
        registry=REGISTRY,
        task_id="review-d1-node-1",
        task_type="review_dispute",
        issuer=issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "11" * 32,
        payload={"dispute_id": "d1", "bundle_hash": "0x" + "22" * 32},
        nonce="1",
        deadline="1770000100",
    )
    typed = build_task_v2_typed_data(task)
    assert typed["domain"]["version"] == "2"
    task["signature"] = "0x" + Account.sign_message(
        encode_typed_data(full_message=typed), issuer.key
    ).signature.hex()
    assert verify_task_v2(
        task,
        expected_chain_id="31337",
        expected_registry=REGISTRY,
        expected_recipient=recipient.address,
        now=1770000000,
    ) == issuer.address

    task["payload"]["bundle_hash"] = "0x" + "33" * 32
    with pytest.raises(LoveEngineError) as exc:
        verify_task_v2(
            task,
            expected_chain_id="31337",
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            now=1770000000,
        )
    assert exc.value.code == "payload_hash_mismatch"
