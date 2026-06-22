"""EIP-712 domain version 2 for live evidence tasks and receipts."""

from __future__ import annotations

from typing import Any

from eth_utils import to_checksum_address

from .network_typed_data import id_hash, payload_hash
from .typed_data import DOMAIN_TYPES, require_bytes32


def _envelope(primary_type: str, fields: list[dict[str, str]], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "types": {"EIP712Domain": DOMAIN_TYPES, primary_type: fields},
        "primaryType": primary_type,
        "domain": {
            "name": "LoveEngine Agent Network",
            "version": "2",
            "chainId": int(task["chain_id"]),
            "verifyingContract": to_checksum_address(task["registry"]),
        },
        "message": {
            "taskId": id_hash(task["task_id"]),
            "taskType": id_hash(task["task_type"]),
            "issuer": to_checksum_address(task["issuer"]),
            "recipient": to_checksum_address(task["recipient"]),
            "manifestHash": require_bytes32(task["manifest_hash"], "manifest_hash"),
            "payloadHash": require_bytes32(task["payload_hash"], "payload_hash"),
            "nonce": int(task["nonce"]),
            "deadline": int(task["deadline"]),
        },
    }


def build_task_v2_typed_data(task: dict[str, Any]) -> dict[str, Any]:
    return _envelope(
        "NetworkTaskV2",
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
        task,
    )


def _domain(chain_id: str, registry: str) -> dict[str, Any]:
    return {
        "name": "LoveEngine Agent Network",
        "version": "2",
        "chainId": int(chain_id),
        "verifyingContract": to_checksum_address(registry),
    }


def build_node_profile_v2_typed_data(
    chain_id: str, registry: str, profile: dict[str, Any]
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            "NodeProfileV2": [
                {"name": "node", "type": "address"},
                {"name": "profileHash", "type": "bytes32"},
                {"name": "sequence", "type": "uint256"},
                {"name": "validUntil", "type": "uint256"},
            ],
        },
        "primaryType": "NodeProfileV2",
        "domain": _domain(chain_id, registry),
        "message": {
            "node": to_checksum_address(profile["node"]),
            "profileHash": payload_hash(profile),
            "sequence": int(profile["sequence"]),
            "validUntil": int(profile["valid_until"]),
        },
    }


def build_bootstrap_v2_typed_data(bootstrap: dict[str, Any]) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            "BootstrapV2": [
                {"name": "publisher", "type": "address"},
                {"name": "directoryHash", "type": "bytes32"},
                {"name": "sequence", "type": "uint256"},
                {"name": "validUntil", "type": "uint256"},
            ],
        },
        "primaryType": "BootstrapV2",
        "domain": _domain(bootstrap["chain_id"], bootstrap["registry"]),
        "message": {
            "publisher": to_checksum_address(bootstrap["publisher"]),
            "directoryHash": require_bytes32(
                bootstrap["directory_hash"], "directory_hash"
            ),
            "sequence": int(bootstrap["sequence"]),
            "validUntil": int(bootstrap["valid_until"]),
        },
    }


def build_receipt_v2_typed_data(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
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
        "domain": _domain(receipt["chain_id"], receipt["registry"]),
        "message": {
            "taskId": id_hash(receipt["task_id"]),
            "node": to_checksum_address(receipt["node"]),
            "status": id_hash(receipt["status"]),
            "resultHash": require_bytes32(receipt["result_hash"], "result_hash"),
            "nonce": int(receipt["nonce"]),
            "completedAt": int(receipt["completed_at"]),
        },
    }
