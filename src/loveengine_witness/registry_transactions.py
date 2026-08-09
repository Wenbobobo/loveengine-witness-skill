"""Fail-closed Sepolia transaction plans for the core SkillRegistry only."""

from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed
from .jsonio import read_json
from .network_typed_data import id_hash
from .registry import ACTIVE_RELEASE_STATUS, SKILL_REGISTRY_READ_ABI
from .schema import validate_schema
from .signer_client import (
    SignerClient,
    canonical_transaction_request,
    transaction_request_hash,
    verify_signed_transaction,
)
from .typed_data import DOMAIN_TYPES


SEPOLIA_CHAIN_ID = "11155111"
MAX_PLAN_TTL_SECONDS = 3600
MAX_GAS = 2_000_000
MAX_FEE_PER_GAS_WEI = 100_000_000_000
MAX_PRIORITY_FEE_PER_GAS_WEI = 5_000_000_000
PUBLISH_RELEASE_SIGNATURE = (
    "publishRelease(bytes32,bytes32,bytes32,bytes32,bytes32)"
)
PUBLISH_RELEASE_ABI = [
    {
        "type": "function",
        "name": "publishRelease",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "skillId", "type": "bytes32"},
            {"name": "versionHash", "type": "bytes32"},
            {"name": "packageHash", "type": "bytes32"},
            {"name": "manifestHash", "type": "bytes32"},
            {"name": "previousVersionHash", "type": "bytes32"},
        ],
        "outputs": [],
    }
]
PLAN_AUTHORIZATION_TYPE = "TransactionPlanAuthorizationV1"
PLAN_AUTHORIZATION_DOMAIN = "LoveEngine Registry Publisher"
PLAN_AUTHORIZATION_VERSION = "1"
ZERO_ADDRESS = "0x" + "00" * 20
ZERO_BYTES32 = "0x" + "00" * 32


def _hex_quantity(value: int, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LoveEngineError("transaction_plan_invalid", field)
    return hex(value)


def _transaction(
    *,
    sender: str,
    recipient: str | None,
    data: str,
    nonce: int,
    gas: int,
    max_fee_per_gas: int,
    max_priority_fee_per_gas: int,
) -> dict[str, Any]:
    return canonical_transaction_request(
        {
            "type": "0x2",
            "chainId": hex(int(SEPOLIA_CHAIN_ID)),
            "from": to_checksum_address(sender),
            "to": to_checksum_address(recipient) if recipient else None,
            "gas": _hex_quantity(gas, "gas"),
            "maxFeePerGas": _hex_quantity(max_fee_per_gas, "maxFeePerGas"),
            "maxPriorityFeePerGas": _hex_quantity(
                max_priority_fee_per_gas, "maxPriorityFeePerGas"
            ),
            "value": "0x0",
            "nonce": _hex_quantity(nonce, "nonce"),
            "data": data,
        }
    )


def _with_plan_hash(value: dict[str, Any]) -> dict[str, Any]:
    plan = dict(value)
    plan["plan_hash"] = sha256_prefixed(canonical_json_bytes(plan))
    validate_transaction_plan(plan, now=int(plan["created_at"]))
    return plan


def _artifact_codes(path: Path) -> tuple[str, str, bytes]:
    try:
        raw = Path(path).read_bytes()
        artifact = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoveEngineError("contract_artifact_invalid", "SkillRegistry") from exc
    try:
        init_code = Web3.to_hex(HexBytes(artifact["bytecode"]["object"]))
        runtime_code = Web3.to_hex(HexBytes(artifact["deployedBytecode"]["object"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise LoveEngineError("contract_artifact_invalid", "SkillRegistry") from exc
    if init_code == "0x" or runtime_code == "0x":
        raise LoveEngineError("contract_artifact_invalid", "empty bytecode")
    return init_code, runtime_code, raw


def build_registry_deploy_plan(
    artifact_path: Path,
    *,
    sender: str,
    nonce: int,
    gas: int,
    max_fee_per_gas: int,
    max_priority_fee_per_gas: int,
    created_at: int,
    expires_at: int,
) -> dict[str, Any]:
    artifact_path = Path(artifact_path)
    init_code, runtime_code, artifact_bytes = _artifact_codes(artifact_path)
    transaction = _transaction(
        sender=sender,
        recipient=None,
        data=init_code,
        nonce=nonce,
        gas=gas,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
    )
    return _with_plan_hash(
        {
            "schema_version": "loveengine.chain-transaction-plan/1",
            "operation": "deploy_skill_registry",
            "created_at": str(created_at),
            "expires_at": str(expires_at),
            "transaction": transaction,
            "request_hash": transaction_request_hash(transaction),
            "method_signature": None,
            "artifact_evidence": {
                "contract": "SkillRegistry",
                "artifact_sha256": sha256_prefixed(artifact_bytes),
                "init_code_keccak256": keccak256_hex(HexBytes(init_code)),
                "runtime_code_keccak256": keccak256_hex(HexBytes(runtime_code)),
            },
            "release_evidence": None,
        }
    )


def _publish_calldata(release: dict[str, Any]) -> str:
    validate_schema(release, "skill-release-v1.schema.json")
    if release["chain_id"] != SEPOLIA_CHAIN_ID:
        raise LoveEngineError("wrong_chain_id", release["chain_id"])
    if release["version_hash"].lower() != id_hash(release["version"]).lower():
        raise LoveEngineError("release_metadata_mismatch", "version_hash")
    if release["status"] != "active" or release.get(
        "replacement_version_hash", ZERO_BYTES32
    ).lower() != ZERO_BYTES32:
        raise LoveEngineError(
            "release_not_publishable", "publishRelease only accepts active releases"
        )
    contract = Web3().eth.contract(abi=PUBLISH_RELEASE_ABI)
    return contract.encode_abi(
        "publishRelease",
        args=[
            id_hash(release["skill_id"]),
            release["version_hash"],
            release["package_hash"],
            release["manifest_hash"],
            release["previous_version_hash"],
        ],
    )


def build_registry_publish_plan(
    release: dict[str, Any],
    *,
    sender: str,
    nonce: int,
    gas: int,
    max_fee_per_gas: int,
    max_priority_fee_per_gas: int,
    created_at: int,
    expires_at: int,
) -> dict[str, Any]:
    sender_address = to_checksum_address(sender)
    if sender_address != to_checksum_address(release["publisher"]):
        raise LoveEngineError("wrong_publisher", sender_address)
    registry = to_checksum_address(release["registry"])
    transaction = _transaction(
        sender=sender_address,
        recipient=registry,
        data=_publish_calldata(release),
        nonce=nonce,
        gas=gas,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
    )
    return _with_plan_hash(
        {
            "schema_version": "loveengine.chain-transaction-plan/1",
            "operation": "publish_release",
            "created_at": str(created_at),
            "expires_at": str(expires_at),
            "transaction": transaction,
            "request_hash": transaction_request_hash(transaction),
            "method_signature": PUBLISH_RELEASE_SIGNATURE,
            "artifact_evidence": None,
            "release_evidence": {
                "registry": registry,
                "publisher": sender_address,
                "skill_id": release["skill_id"],
                "version": release["version"],
                "package_hash": release["package_hash"].lower(),
                "manifest_hash": release["manifest_hash"].lower(),
                "previous_version_hash": release["previous_version_hash"].lower(),
            },
        }
    )


def validate_transaction_plan(
    plan: dict[str, Any], *, now: int | None = None
) -> dict[str, Any]:
    validate_schema(plan, "chain-transaction-plan-v1.schema.json")
    created_at = int(plan["created_at"])
    expires_at = int(plan["expires_at"])
    checked_at = int(time()) if now is None else now
    if expires_at <= created_at or expires_at - created_at > MAX_PLAN_TTL_SECONDS:
        raise LoveEngineError("transaction_plan_invalid", "expiry window")
    if checked_at > expires_at:
        raise LoveEngineError("transaction_plan_expired", plan["expires_at"])
    if created_at > checked_at + 60:
        raise LoveEngineError("transaction_plan_invalid", "created_at in future")
    transaction = canonical_transaction_request(plan["transaction"])
    if transaction != plan["transaction"]:
        raise LoveEngineError("transaction_plan_not_canonical", "transaction")
    if transaction_request_hash(transaction) != plan["request_hash"]:
        raise LoveEngineError("transaction_request_hash_mismatch", plan["request_hash"])
    gas = int(transaction["gas"], 0)
    max_fee = int(transaction["maxFeePerGas"], 0)
    priority_fee = int(transaction["maxPriorityFeePerGas"], 0)
    if gas <= 21_000 or gas > MAX_GAS:
        raise LoveEngineError("transaction_gas_limit_exceeded", transaction["gas"])
    if max_fee <= 0 or max_fee > MAX_FEE_PER_GAS_WEI:
        raise LoveEngineError("transaction_fee_limit_exceeded", transaction["maxFeePerGas"])
    if priority_fee > max_fee or priority_fee > MAX_PRIORITY_FEE_PER_GAS_WEI:
        raise LoveEngineError(
            "transaction_priority_fee_limit_exceeded",
            transaction["maxPriorityFeePerGas"],
        )
    if plan["operation"] == "deploy_skill_registry":
        evidence = plan["artifact_evidence"]
        if (
            transaction["to"] is not None
            or plan["release_evidence"] is not None
            or plan["method_signature"] is not None
            or evidence is None
            or keccak256_hex(HexBytes(transaction["data"]))
            != evidence["init_code_keccak256"]
        ):
            raise LoveEngineError("transaction_plan_invalid", "deployment binding")
    else:
        release = plan["release_evidence"]
        if (
            plan["artifact_evidence"] is not None
            or release is None
            or plan["method_signature"] != PUBLISH_RELEASE_SIGNATURE
            or transaction["to"] != to_checksum_address(release["registry"])
            or transaction["from"] != to_checksum_address(release["publisher"])
        ):
            raise LoveEngineError("transaction_plan_invalid", "release binding")
        synthetic_release = {
            "schema_version": "loveengine.skill-release/1",
            "chain_id": SEPOLIA_CHAIN_ID,
            "registry": release["registry"],
            "publisher": release["publisher"],
            "skill_id": release["skill_id"],
            "version": release["version"],
            "version_hash": id_hash(release["version"]),
            "package_hash": release["package_hash"],
            "manifest_hash": release["manifest_hash"],
            "previous_version_hash": release["previous_version_hash"],
            "status": "active",
        }
        if transaction["data"].lower() != _publish_calldata(synthetic_release).lower():
            raise LoveEngineError("transaction_plan_invalid", "publish calldata")
    view = dict(plan)
    actual_plan_hash = view.pop("plan_hash")
    expected_plan_hash = sha256_prefixed(canonical_json_bytes(view))
    if actual_plan_hash != expected_plan_hash:
        raise LoveEngineError("transaction_plan_hash_mismatch", expected_plan_hash)
    return {
        "valid": True,
        "plan_hash": actual_plan_hash,
        "request_hash": plan["request_hash"],
        "expires_at": plan["expires_at"],
    }


def build_plan_authorization_typed_data(
    plan: dict[str, Any], transaction_hash: str
) -> dict[str, Any]:
    validate_transaction_plan(plan, now=int(plan["created_at"]))
    verifying_contract = plan["transaction"]["to"] or ZERO_ADDRESS
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            PLAN_AUTHORIZATION_TYPE: [
                {"name": "planHash", "type": "bytes32"},
                {"name": "transactionHash", "type": "bytes32"},
                {"name": "publisher", "type": "address"},
                {"name": "expiresAt", "type": "uint256"},
            ],
        },
        "primaryType": PLAN_AUTHORIZATION_TYPE,
        "domain": {
            "name": PLAN_AUTHORIZATION_DOMAIN,
            "version": PLAN_AUTHORIZATION_VERSION,
            "chainId": int(SEPOLIA_CHAIN_ID),
            "verifyingContract": to_checksum_address(verifying_contract),
        },
        "message": {
            "planHash": "0x" + plan["plan_hash"].removeprefix("sha256:"),
            "transactionHash": transaction_hash,
            "publisher": to_checksum_address(plan["transaction"]["from"]),
            "expiresAt": int(plan["expires_at"]),
        },
    }


def sign_transaction_plan(
    plan: dict[str, Any], signer: SignerClient
) -> dict[str, Any]:
    timestamp = int(time())
    validate_transaction_plan(plan, now=timestamp)
    transaction = plan["transaction"]
    if signer.role != "publisher":
        raise LoveEngineError("signer_operation_not_allowed", signer.role)
    if signer.chain_id != SEPOLIA_CHAIN_ID:
        raise LoveEngineError("wrong_chain_id", signer.chain_id)
    if to_checksum_address(signer.address) != to_checksum_address(transaction["from"]):
        raise LoveEngineError("wrong_signer_address", signer.address)
    raw = signer.sign_transaction(transaction, plan["method_signature"])
    normalized = verify_signed_transaction(transaction, signer.address, raw)
    transaction_hash = Web3.to_hex(Web3.keccak(HexBytes(normalized)))
    authorization = build_plan_authorization_typed_data(plan, transaction_hash)
    authorization_signature = signer.sign_typed_data(authorization)
    recovered = Account.recover_message(
        encode_typed_data(full_message=authorization),
        signature=authorization_signature,
    )
    if to_checksum_address(recovered) != to_checksum_address(signer.address):
        raise LoveEngineError("plan_authorization_signer_mismatch", recovered)
    value = {
        "schema_version": "loveengine.signed-chain-transaction/1",
        "plan_hash": plan["plan_hash"],
        "request_hash": plan["request_hash"],
        "signer": to_checksum_address(signer.address),
        "signed_at": str(timestamp),
        "raw_transaction": normalized,
        "transaction_hash": transaction_hash,
        "authorization_typed_data_sha256": sha256_prefixed(
            canonical_json_bytes(authorization)
        ),
        "authorization_signature": authorization_signature,
    }
    validate_schema(value, "signed-chain-transaction-v1.schema.json")
    return value


def submit_signed_transaction_plan(
    plan: dict[str, Any],
    signed: dict[str, Any],
    *,
    rpc_url: str,
    web3: Any | None = None,
    now: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    validate_transaction_plan(plan, now=now)
    validate_schema(signed, "signed-chain-transaction-v1.schema.json")
    if not int(plan["created_at"]) <= int(signed["signed_at"]) <= int(
        plan["expires_at"]
    ):
        raise LoveEngineError("signed_transaction_time_invalid", signed["signed_at"])
    if signed["plan_hash"] != plan["plan_hash"] or signed["request_hash"] != plan["request_hash"]:
        raise LoveEngineError("signed_transaction_plan_mismatch", signed["plan_hash"])
    raw = verify_signed_transaction(
        plan["transaction"], signed["signer"], signed["raw_transaction"]
    )
    transaction_hash = Web3.to_hex(Web3.keccak(HexBytes(raw)))
    if transaction_hash.lower() != signed["transaction_hash"].lower():
        raise LoveEngineError("signed_transaction_hash_mismatch", transaction_hash)
    authorization = build_plan_authorization_typed_data(plan, transaction_hash)
    if signed["authorization_typed_data_sha256"] != sha256_prefixed(
        canonical_json_bytes(authorization)
    ):
        raise LoveEngineError(
            "plan_authorization_hash_mismatch",
            signed["authorization_typed_data_sha256"],
        )
    try:
        authorizer = Account.recover_message(
            encode_typed_data(full_message=authorization),
            signature=signed["authorization_signature"],
        )
    except Exception as exc:
        raise LoveEngineError("plan_authorization_invalid", "signature") from exc
    if to_checksum_address(authorizer) != to_checksum_address(signed["signer"]):
        raise LoveEngineError("plan_authorization_signer_mismatch", authorizer)
    chain = web3 or Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    try:
        if web3 is None and not chain.is_connected():
            raise LoveEngineError("rpc_unavailable", "configured RPC endpoint", 4)
        if str(chain.eth.chain_id) != SEPOLIA_CHAIN_ID:
            raise LoveEngineError("wrong_chain_id", str(chain.eth.chain_id), 4)
        expected_nonce = int(plan["transaction"]["nonce"], 0)
        actual_nonce = int(
            chain.eth.get_transaction_count(plan["transaction"]["from"], "pending")
        )
        if actual_nonce != expected_nonce:
            raise LoveEngineError(
                "transaction_nonce_mismatch", f"expected {expected_nonce}, got {actual_nonce}"
            )
        submitted_hash = Web3.to_hex(chain.eth.send_raw_transaction(raw))
        if submitted_hash.lower() != transaction_hash.lower():
            raise LoveEngineError("rpc_transaction_hash_mismatch", submitted_hash)
        receipt = chain.eth.wait_for_transaction_receipt(
            submitted_hash, timeout=timeout, poll_latency=2
        )
    except LoveEngineError:
        raise
    except Exception as exc:
        raise LoveEngineError(
            "transaction_submission_failed", exc.__class__.__name__, 4
        ) from exc
    if int(receipt["status"]) != 1:
        raise LoveEngineError("transaction_reverted", transaction_hash, 4)
    block_number = int(receipt["blockNumber"])
    post_state: dict[str, Any]
    if plan["operation"] == "deploy_skill_registry":
        contract_address = receipt.get("contractAddress")
        if not contract_address:
            raise LoveEngineError("deployment_address_missing", transaction_hash, 4)
        try:
            runtime_code = bytes(
                chain.eth.get_code(
                    to_checksum_address(contract_address),
                    block_identifier=block_number,
                )
            )
        except Exception as exc:
            raise LoveEngineError(
                "deployment_post_state_unavailable", exc.__class__.__name__, 4
            ) from exc
        runtime_hash = keccak256_hex(runtime_code)
        if runtime_hash != plan["artifact_evidence"]["runtime_code_keccak256"]:
            raise LoveEngineError("runtime_code_hash_mismatch", runtime_hash, 4)
        post_state = {"runtime_code_hash": runtime_hash}
    else:
        release = plan["release_evidence"]
        try:
            contract = chain.eth.contract(
                address=to_checksum_address(release["registry"]),
                abi=SKILL_REGISTRY_READ_ABI,
            )
            raw_release = contract.functions.getRelease(
                to_checksum_address(release["publisher"]),
                id_hash(release["skill_id"]),
                id_hash(release["version"]),
            ).call(block_identifier=block_number)
        except Exception as exc:
            raise LoveEngineError(
                "release_post_state_unavailable", exc.__class__.__name__, 4
            ) from exc
        if (
            len(raw_release) != 6
            or Web3.to_hex(raw_release[0]).lower() != release["package_hash"].lower()
            or Web3.to_hex(raw_release[1]).lower() != release["manifest_hash"].lower()
            or Web3.to_hex(raw_release[2]).lower()
            != release["previous_version_hash"].lower()
            or int(raw_release[4]) != ACTIVE_RELEASE_STATUS
            or int(raw_release[5]) <= 0
        ):
            raise LoveEngineError("release_post_state_mismatch", transaction_hash, 4)
        post_state = {
            "release_status": "active",
            "published_at": str(int(raw_release[5])),
        }
    return {
        "submitted": True,
        "chain_id": SEPOLIA_CHAIN_ID,
        "plan_hash": plan["plan_hash"],
        "request_hash": plan["request_hash"],
        "transaction_hash": transaction_hash,
        "block_number": str(block_number),
        "block_hash": Web3.to_hex(receipt["blockHash"]),
        "contract_address": (
            to_checksum_address(receipt["contractAddress"])
            if receipt.get("contractAddress")
            else None
        ),
        "post_state_verified": True,
        **post_state,
    }
