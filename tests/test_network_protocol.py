from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from jsonschema import Draft202012Validator

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.network_protocol import (
    build_bootstrap,
    build_node_profile,
    build_task,
    verify_bootstrap,
    verify_node_profile,
    verify_task,
)
from loveengine_witness.network_typed_data import (
    build_bootstrap_typed_data,
    build_node_profile_typed_data,
    build_task_typed_data,
)


ROOT = Path(__file__).resolve().parents[1]
CHAIN_ID = "31337"
REGISTRY = "0x" + "1" * 40


def sign(account: object, typed_data: dict) -> str:
    value = Account.sign_message(
        encode_typed_data(full_message=typed_data),
        account.key,
    ).signature.hex()
    return value if value.startswith("0x") else "0x" + value


def validate_schema(name: str, value: dict) -> None:
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def test_signed_profile_and_bootstrap_reject_tampering() -> None:
    publisher = Account.create()
    node = Account.create()
    profile = build_node_profile(
        node=node.address,
        capabilities=["propagate_skill", "observe_broadcast"],
        sequence="1",
        valid_until="2000000000",
    )
    signed_profile = {
        "schema_version": "loveengine.signed-agent-node-profile/1",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "profile": profile,
        "signature": sign(
            node,
            build_node_profile_typed_data(
                CHAIN_ID,
                REGISTRY,
                profile,
            ),
        ),
    }
    validate_schema("signed-agent-node-profile-v1.schema.json", signed_profile)
    assert verify_node_profile(signed_profile, now=1_900_000_000) == node.address

    tampered = copy.deepcopy(signed_profile)
    tampered["profile"]["capabilities"].pop()
    with pytest.raises(LoveEngineError, match="signature"):
        verify_node_profile(tampered, now=1_900_000_000)

    bootstrap = build_bootstrap(
        publisher=publisher.address,
        nodes=[signed_profile],
        sequence="2",
        valid_until="2000000000",
    )
    bootstrap["signature"] = sign(
        publisher,
        build_bootstrap_typed_data(CHAIN_ID, REGISTRY, bootstrap),
    )
    validate_schema("bootstrap-bundle-v1.schema.json", bootstrap)
    assert verify_bootstrap(bootstrap, CHAIN_ID, REGISTRY, now=1_900_000_000)

    bootstrap["directory"][0]["profile"]["sequence"] = "9"
    with pytest.raises(LoveEngineError, match="directory|signature"):
        verify_bootstrap(bootstrap, CHAIN_ID, REGISTRY, now=1_900_000_000)


def test_network_task_binds_registry_recipient_nonce_deadline_and_payload() -> None:
    issuer = Account.create()
    recipient = Account.create()
    task = build_task(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        task_id="task-1",
        task_type="propagate_skill",
        issuer=issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "2" * 64,
        payload={"package_hash": "sha256:" + "3" * 64},
        nonce="7",
        deadline="2000000000",
    )
    task["signature"] = sign(issuer, build_task_typed_data(task))
    validate_schema("network-task-v1.schema.json", task)
    assert verify_task(
        task,
        expected_chain_id=CHAIN_ID,
        expected_registry=REGISTRY,
        expected_recipient=recipient.address,
        expected_issuer=issuer.address,
        expected_manifest_hash=task["manifest_hash"],
        now=1_900_000_000,
    ) == issuer.address

    for field, value in (
        ("chain_id", "1"),
        ("registry", "0x" + "4" * 40),
        ("recipient", "0x" + "5" * 40),
        ("nonce", "8"),
        ("deadline", "1800000000"),
    ):
        changed = copy.deepcopy(task)
        changed[field] = value
        with pytest.raises(LoveEngineError):
            verify_task(
                changed,
                expected_chain_id=CHAIN_ID,
                expected_registry=REGISTRY,
                expected_recipient=recipient.address,
                expected_issuer=issuer.address,
                expected_manifest_hash=task["manifest_hash"],
                now=1_900_000_000,
            )

    malicious_issuer = Account.create()
    malicious = build_task(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        task_id="task-malicious",
        task_type="propagate_skill",
        issuer=malicious_issuer.address,
        recipient=recipient.address,
        manifest_hash=task["manifest_hash"],
        payload={"package_hash": "sha256:" + "3" * 64},
        nonce="8",
        deadline="2000000000",
    )
    malicious["signature"] = sign(
        malicious_issuer, build_task_typed_data(malicious)
    )
    with pytest.raises(LoveEngineError) as exc:
        verify_task(
            malicious,
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=task["manifest_hash"],
            now=1_900_000_000,
        )
    assert exc.value.code == "wrong_issuer"

    with pytest.raises(LoveEngineError) as exc:
        verify_task(
            task,
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash="0x" + "9" * 64,
            now=1_900_000_000,
        )
    assert exc.value.code == "wrong_manifest_hash"


def test_v1_malformed_signature_returns_protocol_error() -> None:
    issuer = Account.create()
    recipient = Account.create()
    task = build_task(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        task_id="malformed-signature",
        task_type="propagate_skill",
        issuer=issuer.address,
        recipient=recipient.address,
        manifest_hash="0x" + "2" * 64,
        payload={"package_hash": "sha256:" + "3" * 64},
        nonce="9",
        deadline="2000000000",
    )
    task["signature"] = "0x" + "0" * 130

    with pytest.raises(LoveEngineError) as exc:
        verify_task(
            task,
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
            expected_recipient=recipient.address,
            expected_issuer=issuer.address,
            expected_manifest_hash=task["manifest_hash"],
            now=1_900_000_000,
        )

    assert exc.value.code == "invalid_signature"
