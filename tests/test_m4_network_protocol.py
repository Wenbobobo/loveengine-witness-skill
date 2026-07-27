from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import (
    build_node_profile_v2,
    build_task_v2,
    verify_task_v2,
)
from loveengine_witness.m4_typed_data import build_task_v2_typed_data
from loveengine_witness.network_typed_data import payload_hash


REGISTRY = "0x00000000000000000000000000000000000000aa"


@pytest.mark.parametrize("task_type", ["propagate_skill", "observe_broadcast"])
def test_v2_rejects_task_types_owned_by_v1(task_type: str) -> None:
    with pytest.raises(LoveEngineError) as exc:
        build_task_v2(
            chain_id="31337",
            registry=REGISTRY,
            task_id=f"legacy-{task_type}",
            task_type=task_type,
            issuer=Account.create().address,
            recipient=Account.create().address,
            manifest_hash="0x" + "11" * 32,
            payload={},
            nonce="1",
            deadline="1770000100",
        )

    assert exc.value.code == "unsupported_task_type"


def test_v2_profile_rejects_capabilities_owned_by_v1() -> None:
    with pytest.raises(LoveEngineError) as exc:
        build_node_profile_v2(
            Account.create().address,
            ["observe_live_text", "observe_broadcast"],
            "1",
            "1770000100",
        )

    assert exc.value.code == "unsupported_capability"


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
        expected_issuer=issuer.address,
        expected_manifest_hash=task["manifest_hash"],
        now=1770000000,
    ) == issuer.address

    task["payload"]["bundle_hash"] = "0x" + "33" * 32
    with pytest.raises(LoveEngineError) as exc:
        verify_task_v2(
            task,
            expected_chain_id="31337",
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=task["manifest_hash"],
            now=1770000000,
        )
    assert exc.value.code == "payload_hash_mismatch"

    malicious_issuer = Account.create()
    malicious = build_task_v2(
        chain_id="31337",
        registry=REGISTRY,
        task_id="review-d1-node-2",
        task_type="review_dispute",
        issuer=malicious_issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "11" * 32,
        payload={"dispute_id": "d1", "bundle_hash": "0x" + "22" * 32},
        nonce="2",
        deadline="1770000100",
    )
    malicious["signature"] = "0x" + Account.sign_message(
        encode_typed_data(full_message=build_task_v2_typed_data(malicious)),
        malicious_issuer.key,
    ).signature.hex()
    with pytest.raises(LoveEngineError) as exc:
        verify_task_v2(
            malicious,
            expected_chain_id="31337",
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=malicious["manifest_hash"],
            now=1770000000,
        )
    assert exc.value.code == "wrong_issuer"


def test_v2_malformed_signature_returns_protocol_error() -> None:
    issuer = Account.create()
    recipient = Account.create()
    task = build_task_v2(
        chain_id="31337",
        registry=REGISTRY,
        task_id="malformed-signature",
        task_type="review_dispute",
        issuer=issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "11" * 32,
        payload={"dispute_id": "d1", "bundle_hash": "0x" + "22" * 32},
        nonce="3",
        deadline="1770000100",
    )
    task["signature"] = "0x" + "0" * 130

    with pytest.raises(LoveEngineError) as exc:
        verify_task_v2(
            task,
            expected_chain_id="31337",
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=task["manifest_hash"],
            now=1770000000,
        )

    assert exc.value.code == "invalid_signature"


def test_legacy_review_payload_requires_a_bytes32_bundle_hash() -> None:
    issuer = Account.create()
    recipient = Account.create()
    task = build_task_v2(
        chain_id="31337",
        registry=REGISTRY,
        task_id="invalid-legacy-review",
        task_type="review_dispute",
        issuer=issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "11" * 32,
        payload={"dispute_id": "d1", "bundle_hash": "not-a-hash"},
        nonce="4",
        deadline="1770000100",
    )
    task["payload_hash"] = payload_hash(task["payload"])
    task["signature"] = "0x" + Account.sign_message(
        encode_typed_data(full_message=build_task_v2_typed_data(task)),
        issuer.key,
    ).signature.hex()

    with pytest.raises(LoveEngineError) as error:
        verify_task_v2(
            task,
            expected_chain_id="31337",
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=task["manifest_hash"],
            now=1770000000,
        )

    assert error.value.code == "invalid_legacy_review_payload"


@pytest.mark.parametrize(
    "secret_key",
    [
        "write_token",
        "write-token",
        "writeToken",
        "operator_token",
        "pilotToken",
        "bearer_token",
    ],
)
def test_v2_task_rejects_pilot_write_token_fields(secret_key: str) -> None:
    with pytest.raises(LoveEngineError) as exc:
        build_task_v2(
            chain_id="31337",
            registry=REGISTRY,
            task_id=f"secret-{secret_key}",
            task_type="review_dispute",
            issuer=Account.create().address,
            recipient=Account.create().address,
            manifest_hash="0x" + "11" * 32,
            payload={
                "dispute_id": "d1",
                "bundle_hash": "0x" + "22" * 32,
                secret_key: "must-not-enter-protocol-storage",
            },
            nonce="4",
            deadline="1770000100",
        )

    assert exc.value.code == "forbidden_secret_field"
