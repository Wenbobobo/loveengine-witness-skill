from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from time import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.signer_client import (
    CLEF_EXTERNAL_API_VERSION,
    _ClefSigner,
    build_signer_client,
    inspect_clef_binary,
    parse_external_signer_config,
    transaction_request_hash,
    verify_clef_evidence_files,
    verify_signed_transaction,
)
from loveengine_witness.hashes import sha256_prefixed


def _config(account: object, **overrides: object) -> dict:
    value = {
        "schema_version": "loveengine.external-signer-config/1",
        "kind": "clef",
        "role": "observation_node",
        "address": account.address,
        "chain_id": "11155111",
        "approval_mode": "manual_confirm",
        "request_timeout_seconds": 180,
        "transport": "ipc",
        "endpoint": str((Path.cwd() / "clef.ipc").resolve()),
        "ruleset_sha256": "sha256:" + "11" * 32,
        "rules_attestation_sha256": "sha256:" + "22" * 32,
        "allowed_typed_data": [
            {
                "domain_name": "LoveEngine Agent Network",
                "domain_version": "2",
                "verifying_contract": "0x" + "12" * 20,
                "primary_type": "TaskReceiptV2",
            }
        ],
        "approved_transaction_request_hashes": [],
    }
    value.update(overrides)
    return value


def _typed_data(account: object) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TaskReceiptV2": [
                {"name": "taskId", "type": "bytes32"},
                {"name": "node", "type": "address"},
                {"name": "status", "type": "bytes32"},
                {"name": "resultHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint256"},
                {"name": "completedAt", "type": "uint256"},
            ],
        },
        "primaryType": "TaskReceiptV2",
        "domain": {
            "name": "LoveEngine Agent Network",
            "version": "2",
            "chainId": 11155111,
            "verifyingContract": "0x" + "12" * 20,
        },
        "message": {
            "taskId": "0x" + "11" * 32,
            "node": account.address,
            "status": "0x" + "22" * 32,
            "resultHash": "0x" + "33" * 32,
            "nonce": 1,
            "completedAt": int(time()),
        },
    }


def test_signer_config_rejects_remote_or_credentialed_http() -> None:
    account = Account.create()
    for endpoint in (
        "http://example.com:8550",
        "http://user:password@127.0.0.1:8550",
        "http://127.0.0.1:8550/?token=secret",
        "http://localhost:8550",
        "http://127.0.0.1:99999",
    ):
        with pytest.raises(LoveEngineError) as error:
            parse_external_signer_config(
                _config(account, transport="http", endpoint=endpoint)
            )
        assert error.value.code == "signer_endpoint_not_loopback"


def test_anvil_signer_is_loopback_chain_31337_only() -> None:
    account = Account.create()
    with pytest.raises(LoveEngineError) as error:
        parse_external_signer_config(
            _config(
                account,
                kind="anvil_rpc",
                transport="http",
                endpoint="http://127.0.0.1:8545",
                approval_mode="unlocked_test",
                request_timeout_seconds=15,
                ruleset_sha256=None,
                rules_attestation_sha256=None,
            )
        )
    assert error.value.code == "invalid_anvil_signer_config"


def test_clef_config_requires_manual_confirmation_window() -> None:
    account = Account.create()
    for override in (
        {"approval_mode": "unlocked_test"},
        {"request_timeout_seconds": 29},
    ):
        with pytest.raises(LoveEngineError) as error:
            parse_external_signer_config(_config(account, **override))
        assert error.value.code == "schema_validation_failed"


def test_clef_evidence_files_must_match_config(tmp_path) -> None:
    account = Account.create()
    ruleset = tmp_path / "ruleset.js"
    attestation = tmp_path / "ruleset.attestation"
    ruleset.write_text("function ApproveTx() {}\n", encoding="utf-8")
    attestation.write_text("operator-confirmed\n", encoding="utf-8")
    config = parse_external_signer_config(
        _config(
            account,
            ruleset_sha256=sha256_prefixed(ruleset.read_bytes()),
            rules_attestation_sha256=sha256_prefixed(attestation.read_bytes()),
        )
    )

    result = verify_clef_evidence_files(
        config,
        ruleset_path=ruleset,
        rules_attestation_path=attestation,
    )
    assert result["verified"] is True

    ruleset.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        verify_clef_evidence_files(
            config,
            ruleset_path=ruleset,
            rules_attestation_path=attestation,
        )
    assert error.value.code == "clef_evidence_hash_mismatch"


def test_clef_binary_requires_exact_hash_and_version(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "clef.exe"
    binary.write_bytes(b"pinned-clef-binary")
    expected = sha256_prefixed(binary.read_bytes())
    monkeypatch.setattr(
        "loveengine_witness.signer_client.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Clef version 1.17.3-stable\n",
            stderr="",
        ),
    )
    result = inspect_clef_binary(binary, expected_sha256=expected)
    assert result["verified"] is True
    assert result["version"] == "1.17.3"

    with pytest.raises(LoveEngineError) as error:
        inspect_clef_binary(binary, expected_sha256="sha256:" + "00" * 32)
    assert error.value.code == "clef_binary_hash_mismatch"


def test_clef_probe_requires_expected_external_api() -> None:
    account = Account.create()
    config = parse_external_signer_config(_config(account))
    calls: list[tuple[str, list]] = []

    def request(method: str, params: list) -> str:
        calls.append((method, params))
        return CLEF_EXTERNAL_API_VERSION

    result = _ClefSigner(config, request=request).probe()
    assert result["connected"] is True
    assert calls == [("account_version", [])]

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=lambda *_: "999.0.0").probe()
    assert error.value.code == "clef_api_version_mismatch"


def test_public_factory_cannot_bypass_clef_runtime_verification() -> None:
    config = parse_external_signer_config(_config(Account.create()))

    with pytest.raises(LoveEngineError) as error:
        build_signer_client(config)

    assert error.value.code == "clef_verified_factory_required"


def test_role_scope_rejects_vote_for_observation_node() -> None:
    account = Account.create()
    with pytest.raises(LoveEngineError) as error:
        parse_external_signer_config(
            _config(
                account,
                allowed_typed_data=[
                    {
                        "domain_name": "LoveEngine Witness DAO",
                        "domain_version": "1",
                        "verifying_contract": "0x" + "34" * 20,
                        "primary_type": "VoteSignature",
                    }
                ],
            )
        )
    assert error.value.code == "signer_role_scope_invalid"


def test_clef_rejects_semantically_invalid_network_task_before_transport() -> None:
    account = Account.create()
    config = parse_external_signer_config(
        _config(
            account,
            role="task_issuer",
            allowed_typed_data=[
                {
                    "domain_name": "LoveEngine Agent Network",
                    "domain_version": "2",
                    "verifying_contract": "0x" + "12" * 20,
                    "primary_type": "NetworkTaskV2",
                }
            ],
        )
    )
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "NetworkTaskV2": [
                {"name": "taskId", "type": "bytes32"},
                {"name": "taskType", "type": "bytes32"},
                {"name": "issuer", "type": "address"},
                {"name": "recipient", "type": "address"},
                {"name": "manifestHash", "type": "bytes32"},
                {"name": "payloadHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "NetworkTaskV2",
        "domain": {
            "name": "LoveEngine Agent Network",
            "version": "2",
            "chainId": 11155111,
            "verifyingContract": "0x" + "12" * 20,
        },
        "message": {
            "taskId": "0x" + "11" * 32,
            "taskType": "0x" + "ff" * 32,
            "issuer": account.address,
            "recipient": "0x" + "00" * 20,
            "manifestHash": "0x" + "22" * 32,
            "payloadHash": "0x" + "33" * 32,
            "nonce": 1,
            "deadline": int(time()) + 300,
        },
    }

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(
            config,
            request=lambda *_: pytest.fail("must not call Clef"),
        ).sign_typed_data(typed)

    assert error.value.code == "typed_data_semantics_invalid"


def test_clef_typed_signature_is_recovered_and_scoped() -> None:
    account = Account.create()
    config = parse_external_signer_config(_config(account))
    typed = _typed_data(account)

    def request(method: str, params: list) -> str:
        assert method == "account_signTypedData"
        assert params == [config.address, typed]
        return Account.sign_message(
            encode_typed_data(full_message=typed), account.key
        ).signature.hex()

    signature = _ClefSigner(config, request=request).sign_typed_data(typed)
    assert signature.startswith("0x")

    typed["domain"]["chainId"] = 1
    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=request).sign_typed_data(typed)
    assert error.value.code == "wrong_chain_id"


def test_clef_rejects_same_primary_type_with_modified_field_schema() -> None:
    account = Account.create()
    config = parse_external_signer_config(_config(account))
    typed = _typed_data(account)
    typed["types"]["TaskReceiptV2"] = [
        {"name": "arbitrary", "type": "bytes32"}
    ]
    typed["message"] = {"arbitrary": "0x" + "99" * 32}

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=lambda *_: pytest.fail("must not call Clef")).sign_typed_data(
            typed
        )
    assert error.value.code == "typed_data_invalid"


def test_clef_rejects_signature_from_another_account() -> None:
    account = Account.create()
    other = Account.create()
    config = parse_external_signer_config(_config(account))
    typed = _typed_data(account)

    def request(_method: str, _params: list) -> str:
        return Account.sign_message(
            encode_typed_data(full_message=typed), other.key
        ).signature.hex()

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=request).sign_typed_data(typed)
    assert error.value.code == "signer_address_mismatch"


def test_clef_challenge_signing_is_observation_node_only() -> None:
    account = Account.create()
    config = parse_external_signer_config(_config(account))
    message = "\n".join(
        (
            "LoveEngine Relay Authentication",
            "schema=loveengine.relay-challenge/1",
            "chain_id=11155111",
            "registry=" + "0x" + "12" * 20,
            "node=" + config.address,
            "nonce=" + "ab" * 32,
        )
    )

    def request(method: str, params: list) -> str:
        assert method == "account_signData"
        assert params[0:2] == ["text/plain", config.address]
        return Account.sign_message(encode_defunct(text=message), account.key).signature.hex()

    signature = _ClefSigner(config, request=request).sign_message(message)
    assert signature.startswith("0x")

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=request).sign_message("blind text")
    assert error.value.code == "message_signing_not_allowed"


def _transaction(account: object) -> dict:
    return {
        "type": "0x2",
        "chainId": hex(11155111),
        "from": account.address,
        "to": "0x" + "34" * 20,
        "gas": hex(120000),
        "maxFeePerGas": hex(30_000_000_000),
        "maxPriorityFeePerGas": hex(2_000_000_000),
        "value": "0x0",
        "nonce": "0x3",
        "data": "0x12345678",
    }


def _publisher_config(account: object, transaction: dict) -> dict:
    return _config(
        account,
        role="publisher",
        allowed_typed_data=[
            {
                "domain_name": "LoveEngine Agent Network",
                "domain_version": "2",
                "verifying_contract": "0x" + "12" * 20,
                "primary_type": "BootstrapV2",
            }
        ],
        approved_transaction_request_hashes=[
            transaction_request_hash(transaction)
        ],
    )


def test_clef_transaction_requires_approved_exact_request_and_rechecks_raw() -> None:
    account = Account.create()
    transaction = _transaction(account)
    config = parse_external_signer_config(_publisher_config(account, transaction))

    def request(method: str, _params: list[object]) -> dict:
        assert method == "account_signTransaction"
        signed = Account.sign_transaction(
            {
                "type": 2,
                "chainId": 11155111,
                "nonce": 3,
                "maxPriorityFeePerGas": 2_000_000_000,
                "maxFeePerGas": 30_000_000_000,
                "gas": 120000,
                "to": transaction["to"],
                "value": 0,
                "data": transaction["data"],
            },
            account.key,
        )
        return {"raw": signed.raw_transaction.hex()}

    raw = _ClefSigner(config, request=request).sign_transaction(transaction)
    assert Account.recover_transaction(raw) == account.address

    changed = {**transaction, "gas": hex(120001)}
    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=request).sign_transaction(changed)
    assert error.value.code == "transaction_request_not_approved"


def test_raw_transaction_with_unplanned_access_list_is_rejected() -> None:
    account = Account.create()
    transaction = _transaction(account)
    request = dict(transaction)
    request.pop("from")
    request["accessList"] = [
        {"address": "0x" + "56" * 20, "storageKeys": []}
    ]
    raw = Account.sign_transaction(request, account.key).raw_transaction.hex()

    with pytest.raises(LoveEngineError) as error:
        verify_signed_transaction(transaction, account.address, raw)

    assert error.value.code == "signed_transaction_mismatch"


def test_clef_rejects_address_correct_but_tampered_signed_transaction() -> None:
    account = Account.create()
    transaction = _transaction(account)
    config = parse_external_signer_config(_publisher_config(account, transaction))

    def request(_method: str, _params: list[object]) -> dict:
        signed = Account.sign_transaction(
            {
                "type": 2,
                "chainId": 11155111,
                "nonce": 3,
                "maxPriorityFeePerGas": 2_000_000_000,
                "maxFeePerGas": 30_000_000_000,
                "gas": 120000,
                "to": transaction["to"],
                "value": 0,
                "data": "0xdeadbeef",
            },
            account.key,
        )
        return {"raw": signed.raw_transaction.hex()}

    with pytest.raises(LoveEngineError) as error:
        _ClefSigner(config, request=request).sign_transaction(transaction)
    assert error.value.code == "signed_transaction_mismatch"
