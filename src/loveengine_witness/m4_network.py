"""M4 Agent task protocol. M3 V1 messages remain unchanged."""

from __future__ import annotations

import re
from time import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_receipt_v2_typed_data,
    build_task_v2_typed_data,
)
from .network_typed_data import payload_hash
from .network_protocol import verify_task_binding
from .schema import validate_schema
from .secrets import reject_secret_fields


TASK_TYPES_V2 = {
    "observe_live_text",
    "review_dispute",
}


def _recover(typed_data: dict[str, Any], signature: str) -> str:
    try:
        return Account.recover_message(
            encode_typed_data(full_message=typed_data), signature=signature
        )
    except (BadSignature, TypeError, ValueError) as exc:
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
    signed_profile: dict[str, Any],
    now: int | None = None,
    *,
    expected_chain_id: str | None = None,
    expected_registry: str | None = None,
) -> str:
    reject_secret_fields(signed_profile)
    validate_schema(signed_profile, "signed-agent-node-profile-v2.schema.json")
    if (
        expected_chain_id is not None
        and signed_profile["chain_id"] != str(expected_chain_id)
    ):
        raise LoveEngineError("wrong_chain_id", "profile chainId mismatch")
    if expected_registry is not None and to_checksum_address(
        signed_profile["registry"]
    ) != to_checksum_address(expected_registry):
        raise LoveEngineError("wrong_registry", "profile Registry mismatch")
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
    nodes: set[str] = set()
    for profile in bootstrap["directory"]:
        node = verify_node_profile_v2(
            profile,
            now=now,
            expected_chain_id=bootstrap["chain_id"],
            expected_registry=bootstrap["registry"],
        )
        if node in nodes:
            raise LoveEngineError("duplicate_directory_node", node)
        nodes.add(node)
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
    expected_issuer: str,
    expected_manifest_hash: str,
    now: int | None = None,
) -> str:
    reject_secret_fields(task)
    validate_schema(task, "network-task-v2.schema.json")
    if task["task_type"] not in TASK_TYPES_V2:
        raise LoveEngineError("unsupported_task_type", task["task_type"])
    if task["task_type"] == "observe_live_text":
        if "schema_version" in task["payload"]:
            validate_schema(
                task["payload"], "observe-live-text-payload-v1.schema.json"
            )
        elif (
            set(task["payload"]) != {"session_id"}
            or not isinstance(task["payload"]["session_id"], str)
            or not task["payload"]["session_id"]
        ):
            raise LoveEngineError(
                "invalid_legacy_observation_payload",
                "legacy observation payload must bind only session_id",
            )
    else:
        if "schema_version" in task["payload"]:
            validate_schema(
                task["payload"], "review-dispute-payload-v1.schema.json"
            )
        elif (
            set(task["payload"]) != {"dispute_id", "bundle_hash"}
            or not isinstance(task["payload"]["dispute_id"], str)
            or not task["payload"]["dispute_id"]
            or not isinstance(task["payload"]["bundle_hash"], str)
            or re.fullmatch(
                r"0x[0-9a-fA-F]{64}",
                task["payload"]["bundle_hash"],
            )
            is None
        ):
            raise LoveEngineError(
                "invalid_legacy_review_payload",
                "legacy review payload must bind only dispute_id and bundle_hash",
            )
    verify_task_binding(
        task,
        expected_chain_id=expected_chain_id,
        expected_registry=expected_registry,
        expected_recipient=expected_recipient,
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
        now=now,
    )
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
