from __future__ import annotations

import copy

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.participant_attestation import (
    build_participant_attestation,
    build_participant_attestation_typed_data,
    verify_participant_attestation,
    verify_participant_attestation_set,
)


CHAIN_ID = "11155111"
REGISTRY = "0x" + "12" * 20
PACKAGE_HASH = "0x" + "21" * 32
MANIFEST_HASH = "0x" + "22" * 32
SERVICE_HASH = "0x" + "23" * 32
RULESET_HASH = "sha256:" + "24" * 32
RULES_ATTESTATION_HASH = "sha256:" + "26" * 32
PROFILE_HASH = "0x" + "25" * 32
INVITE_HASH = "sha256:" + "27" * 32
POLICY_HASH = "sha256:" + "28" * 32


def _signed(
    account: object,
    *,
    operator_group: int = 1,
    network_group: int = 1,
    run_id: str = "invited-pilot-1",
    issued_at: str = "99",
    valid_until: str = "200",
) -> dict:
    value = build_participant_attestation(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        run_id=run_id,
        node=account.address,
        role="observation_node",
        profile_hash=PROFILE_HASH,
        assignment_task_id=f"observe-{account.address[-8:]}",
        assignment_payload_hash="0x" + "29" * 32,
        pilot_invite_hash=INVITE_HASH,
        trust_policy_hash=POLICY_HASH,
        package_hash=PACKAGE_HASH,
        manifest_hash=MANIFEST_HASH,
        service_config_hash=SERVICE_HASH,
        ruleset_sha256=RULESET_HASH,
        rules_attestation_sha256=RULES_ATTESTATION_HASH,
        operator_group_hash="0x" + f"{operator_group:02x}" * 32,
        network_group_hash="0x" + f"{network_group:02x}" * 32,
        issued_at=issued_at,
        valid_until=valid_until,
    )
    value["signature"] = "0x" + Account.sign_message(
        encode_typed_data(
            full_message=build_participant_attestation_typed_data(value)
        ),
        account.key,
    ).signature.hex()
    return value


def _verify(value: dict, *, now: int = 100) -> str:
    return verify_participant_attestation(
        value,
        expected_chain_id=CHAIN_ID,
        expected_registry=REGISTRY,
        expected_run_id="invited-pilot-1",
        expected_profile_hash=PROFILE_HASH,
        expected_package_hash=PACKAGE_HASH,
        expected_manifest_hash=MANIFEST_HASH,
        expected_service_config_hash=SERVICE_HASH,
        now=now,
    )


def test_participant_attestation_binds_node_run_release_and_groups() -> None:
    account = Account.create()
    value = _signed(account)

    assert _verify(value) == account.address

    tampered = copy.deepcopy(value)
    tampered["operator_group_hash"] = "0x" + "ff" * 32
    with pytest.raises(LoveEngineError) as error:
        _verify(tampered)
    assert error.value.code == "invalid_signature"

    with pytest.raises(LoveEngineError) as mismatch:
        verify_participant_attestation(
            value,
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
            expected_run_id="another-run",
            expected_profile_hash=PROFILE_HASH,
            expected_package_hash=PACKAGE_HASH,
            expected_manifest_hash=MANIFEST_HASH,
            expected_service_config_hash=SERVICE_HASH,
            now=100,
        )
    assert mismatch.value.code == "participant_attestation_mismatch"


@pytest.mark.parametrize(
    ("issued_at", "valid_until", "code"),
    [
        ("101", "200", "participant_attestation_not_yet_valid"),
        ("50", "99", "participant_attestation_expired"),
    ],
)
def test_participant_attestation_enforces_anchor_time_window(
    issued_at: str, valid_until: str, code: str
) -> None:
    value = _signed(
        Account.create(), issued_at=issued_at, valid_until=valid_until
    )

    with pytest.raises(LoveEngineError) as error:
        _verify(value, now=100)
    assert error.value.code == code


def test_participant_attestation_rejects_independently_signed_unbounded_ttl() -> None:
    account = Account.create()
    value = _signed(account)
    value["issued_at"] = "100"
    value["valid_until"] = "4000"
    value["signature"] = "0x" + Account.sign_message(
        encode_typed_data(
            full_message=build_participant_attestation_typed_data(value)
        ),
        account.key,
    ).signature.hex()

    with pytest.raises(LoveEngineError) as error:
        _verify(value, now=100)

    assert error.value.code == "participant_attestation_ttl_exceeded"


def test_participant_set_requires_three_nodes_and_two_declared_groups() -> None:
    accounts = [Account.create() for _ in range(3)]
    values = [
        _signed(accounts[0], operator_group=1, network_group=1),
        _signed(accounts[1], operator_group=1, network_group=2),
        _signed(accounts[2], operator_group=2, network_group=2),
    ]
    rulesets = {account.address: RULESET_HASH for account in accounts}
    assignments = {
        account.address: (f"observe-{account.address[-8:]}", "0x" + "29" * 32)
        for account in accounts
    }

    result = verify_participant_attestation_set(
        values,
        expected_nodes=[account.address for account in accounts],
        expected_chain_id=CHAIN_ID,
        expected_registry=REGISTRY,
        expected_run_id="invited-pilot-1",
        expected_package_hash=PACKAGE_HASH,
        expected_manifest_hash=MANIFEST_HASH,
        expected_service_config_hash=SERVICE_HASH,
        expected_rulesets=rulesets,
        expected_profile_hashes={account.address: PROFILE_HASH for account in accounts},
        expected_assignments=assignments,
        expected_pilot_invite_hash=INVITE_HASH,
        expected_trust_policy_hash=POLICY_HASH,
        expected_rules_attestations={
            account.address: RULES_ATTESTATION_HASH for account in accounts
        },
        now=100,
    )

    assert result == {
        "nodes": 3,
        "declared_operator_groups": 2,
        "declared_network_groups": 2,
    }


@pytest.mark.parametrize("dimension", ["operator", "network"])
def test_participant_set_rejects_single_declared_group(dimension: str) -> None:
    accounts = [Account.create() for _ in range(3)]
    values = [
        _signed(
            account,
            operator_group=1 if dimension == "operator" else index,
            network_group=1 if dimension == "network" else index,
        )
        for index, account in enumerate(accounts, start=1)
    ]

    with pytest.raises(LoveEngineError) as error:
        verify_participant_attestation_set(
            values,
            expected_nodes=[account.address for account in accounts],
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
            expected_run_id="invited-pilot-1",
            expected_package_hash=PACKAGE_HASH,
            expected_manifest_hash=MANIFEST_HASH,
            expected_service_config_hash=SERVICE_HASH,
            expected_rulesets={account.address: RULESET_HASH for account in accounts},
            expected_profile_hashes={
                account.address: PROFILE_HASH for account in accounts
            },
            expected_assignments={
                account.address: (
                    f"observe-{account.address[-8:]}",
                    "0x" + "29" * 32,
                )
                for account in accounts
            },
            expected_pilot_invite_hash=INVITE_HASH,
            expected_trust_policy_hash=POLICY_HASH,
            expected_rules_attestations={
                account.address: RULES_ATTESTATION_HASH for account in accounts
            },
            now=100,
        )
    assert error.value.code == "participant_diversity_missing"
