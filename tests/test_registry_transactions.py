from __future__ import annotations

import copy
import json

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from hexbytes import HexBytes
from web3 import Web3

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.network_typed_data import id_hash
from loveengine_witness.registry_transactions import (
    build_registry_deploy_plan,
    build_registry_publish_plan,
    sign_transaction_plan,
    submit_signed_transaction_plan,
    validate_transaction_plan,
)


NOW = 1_800_000_000
REGISTRY = Web3.to_checksum_address("0x" + "34" * 20)


@pytest.fixture(autouse=True)
def _fixed_signing_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "loveengine_witness.registry_transactions.time", lambda: NOW + 1
    )


def _fees() -> dict[str, int]:
    return {
        "gas": 500_000,
        "max_fee_per_gas": 30_000_000_000,
        "max_priority_fee_per_gas": 2_000_000_000,
        "created_at": NOW,
        "expires_at": NOW + 600,
    }


def _artifact(tmp_path) -> object:
    path = tmp_path / "SkillRegistry.json"
    path.write_text(
        json.dumps(
            {
                "bytecode": {"object": "0x6001600055"},
                "deployedBytecode": {"object": "0x60016000"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _release(account: object) -> dict:
    version = "0.7.0-invited-public-pilot"
    return {
        "schema_version": "loveengine.skill-release/1",
        "chain_id": "11155111",
        "registry": REGISTRY,
        "publisher": account.address,
        "skill_id": "loveengine-witness",
        "version": version,
        "version_hash": id_hash(version),
        "package_hash": "0x" + "11" * 32,
        "manifest_hash": "0x" + "22" * 32,
        "previous_version_hash": "0x" + "00" * 32,
        "status": "active",
    }


class _Signer:
    role = "publisher"
    chain_id = "11155111"

    def __init__(self, account: object) -> None:
        self.account = account
        self.address = account.address

    def sign_transaction(self, transaction: dict, _method: str | None = None) -> str:
        request = dict(transaction)
        request.pop("from")
        return Account.sign_transaction(request, self.account.key).raw_transaction.hex()

    def sign_typed_data(self, typed_data: dict) -> str:
        return "0x" + Account.sign_message(
            encode_typed_data(full_message=typed_data), self.account.key
        ).signature.hex()


def test_deploy_plan_binds_artifact_and_rejects_tampering(tmp_path) -> None:
    account = Account.create()
    plan = build_registry_deploy_plan(
        _artifact(tmp_path), sender=account.address, nonce=3, **_fees()
    )
    assert validate_transaction_plan(plan, now=NOW)["valid"] is True
    assert plan["transaction"]["to"] is None
    assert plan["artifact_evidence"]["runtime_code_keccak256"].startswith("0x")

    tampered = copy.deepcopy(plan)
    tampered["transaction"]["data"] = "0x6002600055"
    with pytest.raises(LoveEngineError) as error:
        validate_transaction_plan(tampered, now=NOW)
    assert error.value.code in {
        "transaction_request_hash_mismatch",
        "transaction_plan_invalid",
    }


def test_publish_plan_is_sepolia_zero_value_and_exact_calldata() -> None:
    account = Account.create()
    plan = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **_fees()
    )
    assert validate_transaction_plan(plan, now=NOW)["valid"] is True
    assert plan["transaction"]["chainId"] == "0xaa36a7"
    assert plan["transaction"]["to"] == REGISTRY
    assert plan["transaction"]["value"] == "0x0"
    assert plan["transaction"]["data"].startswith("0xf28cfd07")

    tampered = copy.deepcopy(plan)
    tampered["release_evidence"]["package_hash"] = "0x" + "99" * 32
    with pytest.raises(LoveEngineError) as error:
        validate_transaction_plan(tampered, now=NOW)
    assert error.value.code == "transaction_plan_invalid"

    inactive = _release(account)
    inactive["status"] = "revoked"
    with pytest.raises(LoveEngineError) as error:
        build_registry_publish_plan(
            inactive, sender=account.address, nonce=7, **_fees()
        )
    assert error.value.code == "release_not_publishable"


def test_plan_expiry_and_fee_caps_fail_closed() -> None:
    account = Account.create()
    plan = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **_fees()
    )
    with pytest.raises(LoveEngineError) as error:
        validate_transaction_plan(plan, now=NOW + 601)
    assert error.value.code == "transaction_plan_expired"

    expensive = copy.deepcopy(plan)
    expensive["transaction"]["maxFeePerGas"] = hex(101_000_000_000)
    with pytest.raises(LoveEngineError) as error:
        validate_transaction_plan(expensive, now=NOW)
    assert error.value.code in {
        "transaction_request_hash_mismatch",
        "transaction_fee_limit_exceeded",
    }


def test_expired_plan_is_rejected_before_any_signer_call(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = Account.create()
    plan = build_registry_deploy_plan(
        _artifact(tmp_path), sender=account.address, nonce=3, **_fees()
    )
    monkeypatch.setattr(
        "loveengine_witness.registry_transactions.time", lambda: NOW + 601
    )

    class NeverSigner(_Signer):
        def sign_transaction(self, *_args, **_kwargs) -> str:
            pytest.fail("expired plan must be rejected before the signer is called")

    with pytest.raises(LoveEngineError) as error:
        sign_transaction_plan(plan, NeverSigner(account))

    assert error.value.code == "transaction_plan_expired"


def test_sign_and_submit_revalidate_exact_raw_transaction() -> None:
    account = Account.create()
    plan = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **_fees()
    )
    signed = sign_transaction_plan(plan, _Signer(account))

    class Eth:
        chain_id = 11155111

        def get_transaction_count(self, address: str, state: str) -> int:
            assert address == account.address
            assert state == "pending"
            return 7

        def send_raw_transaction(self, raw: str) -> HexBytes:
            return Web3.keccak(HexBytes(raw))

        def wait_for_transaction_receipt(
            self, transaction_hash: str, *, timeout: int, poll_latency: int
        ) -> dict:
            assert timeout == 120
            assert poll_latency == 2
            return {
                "status": 1,
                "blockNumber": 99,
                "blockHash": HexBytes("0x" + "ab" * 32),
                "contractAddress": None,
            }

        def contract(self, address: str, abi: list) -> object:
            assert address == REGISTRY
            assert abi

            class Call:
                def call(self, *, block_identifier: int) -> tuple:
                    assert block_identifier == 99
                    release = _release(account)
                    return (
                        HexBytes(release["package_hash"]),
                        HexBytes(release["manifest_hash"]),
                        HexBytes(release["previous_version_hash"]),
                        HexBytes("0x" + "00" * 32),
                        1,
                        NOW,
                    )

            class Functions:
                def getRelease(self, *_args: object) -> Call:
                    return Call()

            return type("Contract", (), {"functions": Functions()})()

    result = submit_signed_transaction_plan(
        plan,
        signed,
        rpc_url="https://unused.invalid",
        web3=type("FakeWeb3", (), {"eth": Eth()})(),
        now=NOW + 2,
    )
    assert result["submitted"] is True
    assert result["block_number"] == "99"
    assert result["transaction_hash"] == signed["transaction_hash"]


def test_submit_rejects_nonce_drift_before_broadcast() -> None:
    account = Account.create()
    plan = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **_fees()
    )
    signed = sign_transaction_plan(plan, _Signer(account))

    class Eth:
        chain_id = 11155111

        def get_transaction_count(self, _address: str, _state: str) -> int:
            return 8

        def send_raw_transaction(self, _raw: str) -> None:
            pytest.fail("nonce drift must be rejected before broadcast")

    with pytest.raises(LoveEngineError) as error:
        submit_signed_transaction_plan(
            plan,
            signed,
            rpc_url="https://unused.invalid",
            web3=type("FakeWeb3", (), {"eth": Eth()})(),
            now=NOW + 2,
        )
    assert error.value.code == "transaction_nonce_mismatch"


def test_plan_authorization_rejects_rewrapped_raw_transaction() -> None:
    account = Account.create()
    first = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **_fees()
    )
    signed = sign_transaction_plan(first, _Signer(account))
    second_fees = {**_fees(), "expires_at": NOW + 900}
    second = build_registry_publish_plan(
        _release(account), sender=account.address, nonce=7, **second_fees
    )
    rewrapped = {**signed, "plan_hash": second["plan_hash"]}

    with pytest.raises(LoveEngineError) as error:
        submit_signed_transaction_plan(
            second,
            rewrapped,
            rpc_url="https://unused.invalid",
            web3=type("NeverUsed", (), {})(),
            now=NOW + 2,
        )
    assert error.value.code == "plan_authorization_hash_mismatch"


def test_deploy_submit_verifies_runtime_code_at_receipt_block(tmp_path) -> None:
    account = Account.create()
    plan = build_registry_deploy_plan(
        _artifact(tmp_path), sender=account.address, nonce=3, **_fees()
    )
    signed = sign_transaction_plan(plan, _Signer(account))

    class Eth:
        chain_id = 11155111

        def get_transaction_count(self, _address: str, _state: str) -> int:
            return 3

        def send_raw_transaction(self, raw: str) -> HexBytes:
            return Web3.keccak(HexBytes(raw))

        def wait_for_transaction_receipt(self, *_args, **_kwargs) -> dict:
            return {
                "status": 1,
                "blockNumber": 101,
                "blockHash": HexBytes("0x" + "ab" * 32),
                "contractAddress": "0x" + "56" * 20,
            }

        def get_code(self, _address: str, *, block_identifier: int) -> bytes:
            assert block_identifier == 101
            return HexBytes("0x60016000")

    result = submit_signed_transaction_plan(
        plan,
        signed,
        rpc_url="https://unused.invalid",
        web3=type("FakeWeb3", (), {"eth": Eth()})(),
        now=NOW + 2,
    )
    assert result["post_state_verified"] is True
    assert result["runtime_code_hash"] == plan["artifact_evidence"][
        "runtime_code_keccak256"
    ]
