"""Build and verify signed M3 Agent network messages."""

from __future__ import annotations

from time import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .network_typed_data import (
    build_bootstrap_typed_data,
    build_node_profile_typed_data,
    build_receipt_typed_data,
    build_task_typed_data,
    payload_hash,
)
from .schema import validate_schema
from .secrets import reject_secret_fields


TASK_TYPES = {"propagate_skill", "observe_broadcast"}


def _recover(typed_data: dict[str, Any], signature: str) -> str:
    try:
        return Account.recover_message(
            encode_typed_data(full_message=typed_data),
            signature=signature,
        )
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("invalid_signature", "signature recovery failed") from exc


def _now(value: int | None) -> int:
    return int(time()) if value is None else value


def build_node_profile(
    node: str,
    capabilities: list[str],
    sequence: str,
    valid_until: str,
) -> dict[str, Any]:
    profile = {
        "node": to_checksum_address(node),
        "capabilities": sorted(set(capabilities)),
        "sequence": str(sequence),
        "valid_until": str(valid_until),
    }
    reject_secret_fields(profile)
    return profile


def verify_node_profile(
    signed_profile: dict[str, Any],
    now: int | None = None,
    *,
    expected_chain_id: str | None = None,
    expected_registry: str | None = None,
) -> str:
    reject_secret_fields(signed_profile)
    validate_schema(
        signed_profile,
        "signed-agent-node-profile-v1.schema.json",
    )
    if (
        expected_chain_id is not None
        and signed_profile["chain_id"] != str(expected_chain_id)
    ):
        raise LoveEngineError("wrong_chain_id", "profile chainId mismatch")
    if expected_registry is not None and to_checksum_address(
        signed_profile["registry"]
    ) != to_checksum_address(expected_registry):
        raise LoveEngineError("wrong_registry", "profile Registry mismatch")
    if int(signed_profile["profile"]["valid_until"]) < _now(now):
        raise LoveEngineError("profile_expired", "node profile expired")
    signer = _recover(
        build_node_profile_typed_data(
            signed_profile["chain_id"],
            signed_profile["registry"],
            signed_profile["profile"],
        ),
        signed_profile["signature"],
    )
    expected = to_checksum_address(signed_profile["profile"]["node"])
    if signer != expected:
        raise LoveEngineError("invalid_signature", "node profile signature mismatch")
    return signer


def build_bootstrap(
    publisher: str,
    nodes: list[dict[str, Any]],
    sequence: str,
    valid_until: str,
) -> dict[str, Any]:
    if not nodes:
        raise LoveEngineError("empty_bootstrap", "bootstrap requires nodes")
    chain_id = nodes[0]["chain_id"]
    registry = nodes[0]["registry"]
    value = {
        "schema_version": "loveengine.bootstrap-bundle/1",
        "chain_id": chain_id,
        "registry": registry,
        "publisher": to_checksum_address(publisher),
        "directory": nodes,
        "directory_hash": payload_hash(nodes),
        "sequence": str(sequence),
        "valid_until": str(valid_until),
        "signature": "0x",
    }
    reject_secret_fields(value)
    return value


def verify_bootstrap(
    bootstrap: dict[str, Any],
    expected_chain_id: str,
    expected_registry: str,
    now: int | None = None,
) -> bool:
    reject_secret_fields(bootstrap)
    validate_schema(bootstrap, "bootstrap-bundle-v1.schema.json")
    if bootstrap["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "bootstrap chainId mismatch")
    if to_checksum_address(bootstrap["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "bootstrap Registry mismatch")
    if int(bootstrap["valid_until"]) < _now(now):
        raise LoveEngineError("bootstrap_expired", "bootstrap expired")
    if bootstrap["directory_hash"] != payload_hash(bootstrap["directory"]):
        raise LoveEngineError("directory_hash_mismatch", "directory was modified")
    nodes: set[str] = set()
    for signed_profile in bootstrap["directory"]:
        node = verify_node_profile(
            signed_profile,
            now=now,
            expected_chain_id=bootstrap["chain_id"],
            expected_registry=bootstrap["registry"],
        )
        if node in nodes:
            raise LoveEngineError("duplicate_directory_node", node)
        nodes.add(node)
    signer = _recover(
        build_bootstrap_typed_data(
            bootstrap["chain_id"],
            bootstrap["registry"],
            bootstrap,
        ),
        bootstrap["signature"],
    )
    if signer != to_checksum_address(bootstrap["publisher"]):
        raise LoveEngineError("invalid_signature", "bootstrap signature mismatch")
    return True


def build_task(
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
    if task_type not in TASK_TYPES:
        raise LoveEngineError("unsupported_task_type", task_type)
    value = {
        "schema_version": "loveengine.network-task/1",
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


def verify_task_binding(
    task: dict[str, Any],
    *,
    expected_chain_id: str,
    expected_registry: str,
    expected_recipient: str,
    expected_issuer: str | None = None,
    expected_manifest_hash: str | None = None,
    now: int | None = None,
) -> None:
    """Verify task fields that are invariant across the V1 and V2 codecs."""

    if task["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "task chainId mismatch")
    if to_checksum_address(task["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "task Registry mismatch")
    if to_checksum_address(task["recipient"]) != to_checksum_address(
        expected_recipient
    ):
        raise LoveEngineError("wrong_recipient", "task recipient mismatch")
    if expected_issuer is not None and to_checksum_address(
        task["issuer"]
    ) != to_checksum_address(expected_issuer):
        raise LoveEngineError("wrong_issuer", "task issuer mismatch")
    if (
        expected_manifest_hash is not None
        and task["manifest_hash"].lower() != expected_manifest_hash.lower()
    ):
        raise LoveEngineError("wrong_manifest_hash", "task manifest hash mismatch")
    if int(task["deadline"]) < _now(now):
        raise LoveEngineError("task_expired", "task deadline passed")
    if task["payload_hash"] != payload_hash(task["payload"]):
        raise LoveEngineError("payload_hash_mismatch", "task payload was modified")


def verify_task(
    task: dict[str, Any],
    *,
    expected_chain_id: str,
    expected_registry: str,
    expected_recipient: str,
    expected_issuer: str | None = None,
    expected_manifest_hash: str | None = None,
    now: int | None = None,
) -> str:
    reject_secret_fields(task)
    validate_schema(task, "network-task-v1.schema.json")
    if task["task_type"] not in TASK_TYPES:
        raise LoveEngineError("unsupported_task_type", task["task_type"])
    verify_task_binding(
        task,
        expected_chain_id=expected_chain_id,
        expected_registry=expected_registry,
        expected_recipient=expected_recipient,
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
        now=now,
    )
    signer = _recover(build_task_typed_data(task), task["signature"])
    if signer != to_checksum_address(task["issuer"]):
        raise LoveEngineError("invalid_signature", "task signature mismatch")
    return signer


def build_receipt(
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
    value = {
        "schema_version": "loveengine.task-receipt/1",
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
    reject_secret_fields(value)
    return value


def verify_receipt(
    receipt: dict[str, Any],
    expected_chain_id: str,
    expected_registry: str,
) -> str:
    reject_secret_fields(receipt)
    validate_schema(receipt, "task-receipt-v1.schema.json")
    if receipt["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "receipt chainId mismatch")
    if to_checksum_address(receipt["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "receipt Registry mismatch")
    if receipt["result_hash"] != payload_hash(receipt["result"]):
        raise LoveEngineError("result_hash_mismatch", "receipt result was modified")
    signer = _recover(build_receipt_typed_data(receipt), receipt["signature"])
    if signer != to_checksum_address(receipt["node"]):
        raise LoveEngineError("invalid_signature", "receipt signature mismatch")
    return signer
