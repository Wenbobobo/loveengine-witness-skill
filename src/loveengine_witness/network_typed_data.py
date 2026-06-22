"""EIP-712 builders for the M3 Agent network protocol."""

from __future__ import annotations

from typing import Any

from eth_utils import to_checksum_address

from .canonical import canonical_json_bytes
from .hashes import keccak256_hex
from .typed_data import DOMAIN_TYPES, require_bytes32


DOMAIN_NAME = "LoveEngine Agent Network"
DOMAIN_VERSION = "1"


def network_domain(chain_id: str, registry: str) -> dict[str, Any]:
    return {
        "name": DOMAIN_NAME,
        "version": DOMAIN_VERSION,
        "chainId": int(chain_id),
        "verifyingContract": to_checksum_address(registry),
    }


def _envelope(
    primary_type: str,
    fields: list[dict[str, str]],
    chain_id: str,
    registry: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            primary_type: fields,
        },
        "primaryType": primary_type,
        "domain": network_domain(chain_id, registry),
        "message": message,
    }


def payload_hash(value: Any) -> str:
    return keccak256_hex(canonical_json_bytes(value))


def id_hash(value: str) -> str:
    return keccak256_hex(value.encode("utf-8"))


def build_node_profile_typed_data(
    chain_id: str,
    registry: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    return _envelope(
        "NodeProfile",
        [
            {"name": "node", "type": "address"},
            {"name": "profileHash", "type": "bytes32"},
            {"name": "sequence", "type": "uint256"},
            {"name": "validUntil", "type": "uint256"},
        ],
        chain_id,
        registry,
        {
            "node": to_checksum_address(profile["node"]),
            "profileHash": payload_hash(profile),
            "sequence": int(profile["sequence"]),
            "validUntil": int(profile["valid_until"]),
        },
    )


def build_bootstrap_typed_data(
    chain_id: str,
    registry: str,
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    directory_hash = bootstrap.get("directory_hash") or payload_hash(
        bootstrap["directory"]
    )
    return _envelope(
        "Bootstrap",
        [
            {"name": "publisher", "type": "address"},
            {"name": "directoryHash", "type": "bytes32"},
            {"name": "sequence", "type": "uint256"},
            {"name": "validUntil", "type": "uint256"},
        ],
        chain_id,
        registry,
        {
            "publisher": to_checksum_address(bootstrap["publisher"]),
            "directoryHash": require_bytes32(directory_hash, "directory_hash"),
            "sequence": int(bootstrap["sequence"]),
            "validUntil": int(bootstrap["valid_until"]),
        },
    )


def build_task_typed_data(task: dict[str, Any]) -> dict[str, Any]:
    return _envelope(
        "NetworkTask",
        [
            {"name": "taskId", "type": "bytes32"},
            {"name": "taskType", "type": "bytes32"},
            {"name": "issuer", "type": "address"},
            {"name": "recipient", "type": "address"},
            {"name": "manifestHash", "type": "bytes32"},
            {"name": "payloadHash", "type": "bytes32"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
        ],
        task["chain_id"],
        task["registry"],
        {
            "taskId": id_hash(task["task_id"]),
            "taskType": id_hash(task["task_type"]),
            "issuer": to_checksum_address(task["issuer"]),
            "recipient": to_checksum_address(task["recipient"]),
            "manifestHash": require_bytes32(
                task["manifest_hash"],
                "manifest_hash",
            ),
            "payloadHash": require_bytes32(task["payload_hash"], "payload_hash"),
            "nonce": int(task["nonce"]),
            "deadline": int(task["deadline"]),
        },
    )


def build_receipt_typed_data(receipt: dict[str, Any]) -> dict[str, Any]:
    return _envelope(
        "TaskReceipt",
        [
            {"name": "taskId", "type": "bytes32"},
            {"name": "node", "type": "address"},
            {"name": "status", "type": "bytes32"},
            {"name": "resultHash", "type": "bytes32"},
            {"name": "nonce", "type": "uint256"},
            {"name": "completedAt", "type": "uint256"},
        ],
        receipt["chain_id"],
        receipt["registry"],
        {
            "taskId": id_hash(receipt["task_id"]),
            "node": to_checksum_address(receipt["node"]),
            "status": id_hash(receipt["status"]),
            "resultHash": require_bytes32(receipt["result_hash"], "result_hash"),
            "nonce": int(receipt["nonce"]),
            "completedAt": int(receipt["completed_at"]),
        },
    )
