"""M4 Agent task protocol. M3 V1 messages remain unchanged."""

from __future__ import annotations

from time import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_receipt_v2_typed_data,
    build_task_v2_typed_data,
)
from .network_typed_data import payload_hash
from .schema import validate_schema
from .secrets import reject_secret_fields


TASK_TYPES_V2 = {
    "propagate_skill",
    "observe_broadcast",
    "observe_live_text",
    "review_dispute",
}


def _recover(typed_data: dict[str, Any], signature: str) -> str:
    try:
        return Account.recover_message(
            encode_typed_data(full_message=typed_data), signature=signature
        )
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("invalid_signature", "signature recovery failed") from exc


def build_node_profile_v2(
    node: str, capabilities: list[str], sequence: str, valid_until: str
) -> dict[str, Any]:
    unsupported = set(capabilities) - TASK_TYPES_V2
    if unsupported:
        raise LoveEngineError("unsupported_capability", sorted(unsupported)[0])
    return {
        "node": to_checksum_address(node),
        "capabilities": sorted(set(capabilities)),
        "sequence": str(sequence),
        "valid_until": str(valid_until),
    }


def verify_node_profile_v2(
    signed_profile: dict[str, Any], now: int | None = None
) -> str:
    reject_secret_fields(signed_profile)
    validate_schema(signed_profile, "signed-agent-node-profile-v2.schema.json")
    if int(signed_profile["profile"]["valid_until"]) < (
        int(time()) if now is None else now
    ):
        raise LoveEngineError("profile_expired", "node profile expired")
    signer = _recover(
        build_node_profile_v2_typed_data(
            signed_profile["chain_id"],
            signed_profile["registry"],
            signed_profile["profile"],
        ),
        signed_profile["signature"],
    )
    if signer != to_checksum_address(signed_profile["profile"]["node"]):
        raise LoveEngineError("invalid_signature", "node profile signature mismatch")
    return signer


def build_bootstrap_v2(
    *,
    chain_id: str,
    registry: str,
    publisher: str,
    nodes: list[dict[str, Any]],
    sequence: str,
    valid_until: str,
) -> dict[str, Any]:
    if not nodes:
        raise LoveEngineError("empty_bootstrap", "bootstrap requires nodes")
    return {
        "schema_version": "loveengine.bootstrap-bundle/2",
        "chain_id": str(chain_id),
        "registry": to_checksum_address(registry),
        "publisher": to_checksum_address(publisher),
        "directory": nodes,
        "directory_hash": payload_hash(nodes),
        "sequence": str(sequence),
        "valid_until": str(valid_until),
        "signature": "0x",
    }


def verify_bootstrap_v2(
    bootstrap: dict[str, Any],
    expected_chain_id: str,
    expected_registry: str,
    now: int | None = None,
) -> bool:
    reject_secret_fields(bootstrap)
    validate_schema(bootstrap, "bootstrap-bundle-v2.schema.json")
    if bootstrap["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "bootstrap chainId mismatch")
    if to_checksum_address(bootstrap["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "bootstrap Registry mismatch")
    if int(bootstrap["valid_until"]) < (int(time()) if now is None else now):
        raise LoveEngineError("bootstrap_expired", "bootstrap expired")
    if bootstrap["directory_hash"] != payload_hash(bootstrap["directory"]):
        raise LoveEngineError("directory_hash_mismatch", "directory was modified")
    for profile in bootstrap["directory"]:
        verify_node_profile_v2(profile, now=now)
    signer = _recover(
        build_bootstrap_v2_typed_data(bootstrap), bootstrap["signature"]
    )
    if signer != to_checksum_address(bootstrap["publisher"]):
        raise LoveEngineError("invalid_signature", "bootstrap signature mismatch")
    return True


def build_task_v2(
    *,
    chain_id: str,
    registry: str,
    task_id: str,
    task_type: str,
    issuer: str,
    recipient: str,
    manifest_hash: str,
    payload: dict[str, Any],
    nonce: str,
    deadline: str,
) -> dict[str, Any]:
    if task_type not in TASK_TYPES_V2:
        raise LoveEngineError("unsupported_task_type", task_type)
    value = {
        "schema_version": "loveengine.network-task/2",
        "chain_id": str(chain_id),
        "registry": to_checksum_address(registry),
        "task_id": task_id,
        "task_type": task_type,
        "issuer": to_checksum_address(issuer),
        "recipient": to_checksum_address(recipient),
        "manifest_hash": manifest_hash.lower(),
        "payload": payload,
        "payload_hash": payload_hash(payload),
        "nonce": str(nonce),
        "deadline": str(deadline),
        "signature": "0x",
    }
    reject_secret_fields(value)
    return value


def verify_task_v2(
    task: dict[str, Any],
    *,
    expected_chain_id: str,
    expected_registry: str,
    expected_recipient: str,
    now: int | None = None,
) -> str:
    reject_secret_fields(task)
    validate_schema(task, "network-task-v2.schema.json")
    if task["task_type"] not in TASK_TYPES_V2:
        raise LoveEngineError("unsupported_task_type", task["task_type"])
    if task["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "task chainId mismatch")
    if to_checksum_address(task["registry"]) != to_checksum_address(expected_registry):
        raise LoveEngineError("wrong_registry", "task Registry mismatch")
    if to_checksum_address(task["recipient"]) != to_checksum_address(expected_recipient):
        raise LoveEngineError("wrong_recipient", "task recipient mismatch")
    if int(task["deadline"]) < (int(time()) if now is None else now):
        raise LoveEngineError("task_expired", "task deadline passed")
    if task["payload_hash"] != payload_hash(task["payload"]):
        raise LoveEngineError("payload_hash_mismatch", "task payload was modified")
    signer = _recover(build_task_v2_typed_data(task), task["signature"])
    if signer != to_checksum_address(task["issuer"]):
        raise LoveEngineError("invalid_signature", "task signature mismatch")
    return signer


def build_receipt_v2(
    *,
    chain_id: str,
    registry: str,
    task_id: str,
    node: str,
    status: str,
    result: dict[str, Any],
    nonce: str,
    completed_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "loveengine.task-receipt/2",
        "chain_id": str(chain_id),
        "registry": to_checksum_address(registry),
        "task_id": task_id,
        "node": to_checksum_address(node),
        "status": status,
        "result": result,
        "result_hash": payload_hash(result),
        "nonce": str(nonce),
        "completed_at": str(completed_at),
        "signature": "0x",
    }


def verify_receipt_v2(
    receipt: dict[str, Any], expected_chain_id: str, expected_registry: str
) -> str:
    reject_secret_fields(receipt)
    validate_schema(receipt, "task-receipt-v2.schema.json")
    if receipt["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "receipt chainId mismatch")
    if to_checksum_address(receipt["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "receipt Registry mismatch")
    if receipt["result_hash"] != payload_hash(receipt["result"]):
        raise LoveEngineError("result_hash_mismatch", "receipt result was modified")
    signer = _recover(build_receipt_v2_typed_data(receipt), receipt["signature"])
    if signer != to_checksum_address(receipt["node"]):
        raise LoveEngineError("invalid_signature", "receipt signature mismatch")
    return signer
