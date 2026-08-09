from __future__ import annotations

import copy

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

import loveengine_witness.core_transcript as core_transcript_module
from loveengine_witness.core_transcript import (
    core_transcript_hash,
    public_service_config_hash,
    select_verification_anchor,
    verify_core_transcript,
)
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.participant_attestation import (
    build_participant_attestation,
    build_participant_attestation_typed_data,
)
from loveengine_witness.schema import validate_schema
from test_core_transcript import _core_fixture


SHA256_A = "sha256:" + "a1" * 32
SHA256_B = "sha256:" + "b2" * 32
SHA256_C = "sha256:" + "c3" * 32


def _sign_attestation(account: object, value: dict) -> dict:
    value["signature"] = "0x" + Account.sign_message(
        encode_typed_data(
            full_message=build_participant_attestation_typed_data(value)
        ),
        account.key,
    ).signature.hex()
    return value


def _v2_core_fixture() -> tuple[dict, list[object]]:
    value = _core_fixture()
    accounts = [Account.create() for _ in range(3)]
    for receipt, account in zip(value["observation_receipts"], accounts, strict=True):
        receipt["node"] = account.address
    for receipt, account in zip(value["review_receipts"], accounts, strict=True):
        receipt["node"] = account.address
    for task, account in zip(value["network_tasks"][:3], accounts, strict=True):
        task["recipient"] = account.address
    for task, account in zip(value["network_tasks"][3:], accounts, strict=True):
        task["recipient"] = account.address
    for profile, account in zip(
        value["bootstrap"]["directory"], accounts, strict=True
    ):
        profile["profile"]["node"] = account.address

    service = {
        "schema_version": "loveengine.invited-pilot-service-config/1",
        "transport": "loopback",
        "participant_origin": "http://127.0.0.1:8780",
        "relay_url": "ws://127.0.0.1:8780/v1/ws",
        "tailnet_only": True,
        "funnel_enabled": False,
        "admin_surface": "loopback_only",
        "participant_allowlist_hash": "0x" + "44" * 32,
        "tailscale_serve_config_hash": "0x" + "55" * 32,
        "restore_verified": True,
    }
    service["config_hash"] = public_service_config_hash(service)
    publisher = value["chain"]["publisher"]
    task_issuers = {item["issuer"] for item in value["network_tasks"]}
    publisher_roles = ["publisher"]
    if publisher in task_issuers:
        publisher_roles.append("task_issuer")
    signer_evidence = [
        {
            "address": publisher,
            "roles": publisher_roles,
            "backend": "anvil-test",
            "approval_mode": "unlocked_test",
            "ruleset_sha256": SHA256_A,
            "rules_attestation_sha256": SHA256_B,
            "audit_log_sha256": SHA256_C,
        }
    ]
    for issuer in task_issuers - {publisher}:
        signer_evidence.append(
            {
                "address": issuer,
                "roles": ["task_issuer"],
                "backend": "anvil-test",
                "approval_mode": "unlocked_test",
                "ruleset_sha256": SHA256_A,
                "rules_attestation_sha256": SHA256_B,
                "audit_log_sha256": SHA256_C,
            }
        )
    for account in accounts:
        signer_evidence.append(
            {
                "address": account.address,
                "roles": ["node"],
                "backend": "anvil-test",
                "approval_mode": "unlocked_test",
                "ruleset_sha256": SHA256_A,
                "rules_attestation_sha256": SHA256_B,
                "audit_log_sha256": SHA256_C,
            }
        )
    attestations = []
    for index, account in enumerate(accounts):
        attestations.append(
            _sign_attestation(
                account,
                build_participant_attestation(
                    chain_id=value["chain"]["chain_id"],
                    registry=value["chain"]["registry"],
                    run_id=value["run_id"],
                    node=account.address,
                    role="observation_node",
                    profile_hash=core_transcript_module.payload_hash(
                        value["bootstrap"]["directory"][index]["profile"]
                    ),
                    assignment_task_id=value["network_tasks"][index]["task_id"],
                    assignment_payload_hash=value["network_tasks"][index][
                        "payload_hash"
                    ],
                    pilot_invite_hash="sha256:" + "44" * 32,
                    trust_policy_hash="sha256:" + "55" * 32,
                    package_hash=value["release_anchor"]["package_hash"],
                    manifest_hash=value["release_anchor"]["manifest_hash"],
                    service_config_hash=service["config_hash"],
                    ruleset_sha256=SHA256_A,
                    rules_attestation_sha256=SHA256_B,
                    operator_group_hash="0x" + f"{1 + index // 2:02x}" * 32,
                    network_group_hash="0x" + f"{1 + (index + 1) // 2:02x}" * 32,
                    issued_at="1769999000",
                    valid_until="1770001000",
                ),
            )
        )
    value.update(
        {
            "schema_version": "loveengine.witness-core-transcript/2",
            "source_commit": "ab" * 20,
            "protocol": "loveengine-witness-net/0.6",
            "input_mode": "synthetic_fixture",
            "pilot_invite_hash": "sha256:" + "44" * 32,
            "trust_policy_hash": "sha256:" + "55" * 32,
            "public_service": service,
            "signer_evidence": signer_evidence,
            "participant_attestations": attestations,
            "acceptance": {
                "duration_seconds": 900,
                "event_count": 30,
                "observer_count": 10,
                "restart_verified": True,
                "reconnect_verified": True,
                "cleanup_verified": True,
                "secret_findings": 0,
                "stderr_empty": True,
            },
            "does_not_prove": [
                "content_truth",
                "participant_independence",
                "production_availability",
                "enterprise_identity",
                "signer_backend_or_rules_enforcement",
                "tailscale_configuration_authenticity",
            ],
        }
    )
    value["chain"].update(
        {
            "deployment_transaction": "0x" + "66" * 32,
            "runtime_code_hash": "0x" + "77" * 32,
            "release_transaction": "0x" + "88" * 32,
            "release_status": "active",
            "primary_rpc_observation_hash": "sha256:" + "66" * 32,
            "secondary_rpc_observation_hash": "sha256:" + "77" * 32,
        }
    )
    value["verification_anchor"]["selected_via"] = "latest"
    value["transcript_hash"] = core_transcript_hash(value)
    return value, accounts


def _mock_stages(
    monkeypatch: pytest.MonkeyPatch, accounts: list[object]
) -> None:
    members = {
        account.address: {"observe_live_text", "review_dispute"}
        for account in accounts
    }
    monkeypatch.setattr(
        core_transcript_module,
        "verify_witness_evidence_stages",
        lambda value, allowed_issuers=None: (members, {}),
    )


def test_v2_offline_verifies_signed_participant_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, accounts = _v2_core_fixture()
    _mock_stages(monkeypatch, accounts)

    result = verify_core_transcript(value)

    assert result["verification_level"] == "offline_integrity"
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False
    assert result["chain_consistency_checked"] is False
    assert result["participant_claims_verified"] is True
    assert result["signer_backend_evidence_verified"] is False
    assert result["nodes"] == 3
    assert result["declared_operator_groups"] == 2
    assert result["declared_network_groups"] == 2


def test_v2_requires_two_independent_rpc_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, accounts = _v2_core_fixture()
    _mock_stages(monkeypatch, accounts)
    observation_hash = "sha256:" + "91" * 32
    value["chain"]["primary_rpc_observation_hash"] = observation_hash
    value["chain"]["secondary_rpc_observation_hash"] = observation_hash
    value["transcript_hash"] = core_transcript_hash(value)
    monkeypatch.setattr(
        core_transcript_module,
        "observe_release_anchor_rpc",
        lambda _value, _url: observation_hash,
    )

    with pytest.raises(LoveEngineError) as error:
        verify_core_transcript(value, rpc_url="http://127.0.0.1:8545")
    assert error.value.code == "two_rpc_endpoints_required"

    result = verify_core_transcript(
        value,
        rpc_url="http://127.0.0.1:8545",
        secondary_rpc_url="http://127.0.0.1:9545",
    )
    assert result["verification_level"] == "chain_consistency"
    assert result["chain_consistency_checked"] is True
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False


def test_rpc_observation_binds_safe_anchor_code_and_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, _ = _v2_core_fixture()
    value["environment"] = "sepolia_invited_pilot"
    value["chain"]["chain_id"] = "11155111"
    value["verification_anchor"]["selected_via"] = "safe"
    code = b"registry-runtime"
    value["chain"]["runtime_code_hash"] = Web3.to_hex(Web3.keccak(code))

    class Eth:
        chain_id = 11155111

        def get_block(self, identifier: object) -> dict:
            if identifier == "safe":
                return {"number": 20, "hash": b"\x01" * 32, "timestamp": 10}
            return {"number": 9, "hash": b"\x02" * 32, "timestamp": 9}

        def get_code(self, _address: str, *, block_identifier: int) -> bytes:
            assert block_identifier == int(value["verification_anchor"]["block_number"])
            return code

        def get_transaction_receipt(self, transaction_hash: str) -> dict:
            deploy = transaction_hash == value["chain"]["deployment_transaction"]
            return {
                "status": 1,
                "blockNumber": 8 if deploy else 9,
                "blockHash": b"\x03" * 32 if deploy else b"\x04" * 32,
                "contractAddress": value["chain"]["registry"] if deploy else None,
            }

        def get_transaction(self, transaction_hash: str) -> dict:
            deploy = transaction_hash == value["chain"]["deployment_transaction"]
            return {
                "from": value["chain"]["publisher"],
                "to": None if deploy else value["chain"]["registry"],
                "input": (
                    "0x6000"
                    if deploy
                    else core_transcript_module._expected_publish_release_calldata(
                        value
                    )
                ),
            }

    fake = type("FakeWeb3", (), {"eth": Eth()})()
    monkeypatch.setattr(
        core_transcript_module,
        "verify_release_anchor_rpc",
        lambda _value, _url, web3=None: (
            web3,
            int(value["verification_anchor"]["block_number"]),
        ),
    )
    digest = core_transcript_module.observe_release_anchor_rpc(
        value, "https://primary.invalid", web3=fake
    )
    assert digest.startswith("sha256:")


def test_rpc_observation_rejects_publish_calldata_for_another_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, _ = _v2_core_fixture()
    value["environment"] = "sepolia_invited_pilot"
    value["chain"]["chain_id"] = "11155111"
    value["verification_anchor"]["selected_via"] = "safe"
    code = b"registry-runtime"
    value["chain"]["runtime_code_hash"] = Web3.to_hex(Web3.keccak(code))

    class Eth:
        chain_id = 11155111

        def get_block(self, identifier: object) -> dict:
            number = 20 if identifier == "safe" else 9
            return {"number": number, "hash": b"\x01" * 32, "timestamp": 10}

        def get_code(self, _address: str, *, block_identifier: int) -> bytes:
            return code

        def get_transaction_receipt(self, transaction_hash: str) -> dict:
            deploy = transaction_hash == value["chain"]["deployment_transaction"]
            return {
                "status": 1,
                "blockNumber": 8 if deploy else 9,
                "blockHash": b"\x03" * 32,
                "contractAddress": value["chain"]["registry"] if deploy else None,
            }

        def get_transaction(self, transaction_hash: str) -> dict:
            deploy = transaction_hash == value["chain"]["deployment_transaction"]
            calldata = core_transcript_module._expected_publish_release_calldata(value)
            return {
                "from": value["chain"]["publisher"],
                "to": None if deploy else value["chain"]["registry"],
                "input": "0x6000" if deploy else calldata[:-2] + "01",
            }

    fake = type("FakeWeb3", (), {"eth": Eth()})()
    monkeypatch.setattr(
        core_transcript_module,
        "verify_release_anchor_rpc",
        lambda _value, _url, web3=None: (
            web3,
            int(value["verification_anchor"]["block_number"]),
        ),
    )

    with pytest.raises(LoveEngineError) as error:
        core_transcript_module.observe_release_anchor_rpc(
            value, "https://primary.invalid", web3=fake
        )

    assert error.value.code == "chain_transaction_invalid"


def test_v2_rejects_public_service_config_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, accounts = _v2_core_fixture()
    _mock_stages(monkeypatch, accounts)
    value["public_service"]["participant_origin"] = "http://127.0.0.1:9999"
    value["transcript_hash"] = core_transcript_hash(value)

    with pytest.raises(LoveEngineError) as error:
        verify_core_transcript(value)
    assert error.value.code == "public_service_config_hash_mismatch"


def test_v2_requires_signer_evidence_for_every_receipt_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, accounts = _v2_core_fixture()
    _mock_stages(monkeypatch, accounts)
    value["signer_evidence"] = [
        item
        for item in value["signer_evidence"]
        if item["address"] != accounts[0].address
    ]
    value["transcript_hash"] = core_transcript_hash(value)

    with pytest.raises(LoveEngineError) as error:
        verify_core_transcript(value)
    assert error.value.code == "signer_evidence_missing"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["public_service"].update(
            {"participant_origin": "http://pilot.ts.net"}
        ),
        lambda value: value["public_service"].update(
            {"relay_url": "ws://pilot.ts.net/v1/ws"}
        ),
        lambda value: value["public_service"].update({"tailnet_only": False}),
        lambda value: value["signer_evidence"][0].update(
            {"backend": "anvil-test"}
        ),
        lambda value: value["signer_evidence"][0].update(
            {"approval_mode": "unlocked_test"}
        ),
    ],
)
def test_v2_sepolia_schema_requires_secure_tailnet_and_clef(mutate: object) -> None:
    value, _ = _v2_core_fixture()
    value["environment"] = "sepolia_invited_pilot"
    value["chain"]["chain_id"] = "11155111"
    value["verification_anchor"]["selected_via"] = "safe"
    value["public_service"].update(
        {
            "transport": "tailscale_serve",
            "participant_origin": "https://pilot.ts.net",
            "relay_url": "wss://pilot.ts.net/v1/ws",
            "tailnet_only": True,
        }
    )
    for item in value["signer_evidence"]:
        item["backend"] = "clef"
        item["approval_mode"] = "manual_confirm"
    validate_schema(value, "witness-core-transcript-v2.schema.json")
    mutate(value)

    with pytest.raises(LoveEngineError) as error:
        validate_schema(value, "witness-core-transcript-v2.schema.json")
    assert error.value.code == "schema_validation_failed"


class _FakeEth:
    def __init__(self, chain_id: int, *, fail_safe: bool = False) -> None:
        self.chain_id = chain_id
        self.fail_safe = fail_safe
        self.requests: list[str] = []

    def get_block(self, tag: str) -> dict:
        self.requests.append(tag)
        if tag == "safe" and self.fail_safe:
            raise RuntimeError("safe unavailable")
        return {"number": 10, "hash": bytes.fromhex("11" * 32), "timestamp": 100}


class _FakeWeb3:
    def __init__(self, chain_id: int, *, fail_safe: bool = False) -> None:
        self.eth = _FakeEth(chain_id, fail_safe=fail_safe)


def test_safe_anchor_selection_never_falls_back_to_latest() -> None:
    w3 = _FakeWeb3(11155111)

    anchor = select_verification_anchor(
        w3, environment="sepolia_invited_pilot"
    )

    assert anchor["selected_via"] == "safe"
    assert w3.eth.requests == ["safe"]

    unavailable = _FakeWeb3(11155111, fail_safe=True)
    with pytest.raises(LoveEngineError) as error:
        select_verification_anchor(
            unavailable, environment="sepolia_invited_pilot"
        )
    assert error.value.code == "verification_anchor_unavailable"
    assert unavailable.eth.requests == ["safe"]


def test_safe_anchor_rejects_non_sepolia_chain() -> None:
    with pytest.raises(LoveEngineError) as error:
        select_verification_anchor(
            _FakeWeb3(31337), environment="sepolia_invited_pilot"
        )
    assert error.value.code == "wrong_chain_id"
