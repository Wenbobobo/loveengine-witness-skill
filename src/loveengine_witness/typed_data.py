"""EIP-712 typed-data builders matching WitnessDAO."""

from __future__ import annotations

import re
from typing import Any

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .secrets import reject_secret_fields


BYTES32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
DOMAIN_TYPES = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]


def domain(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "LoveEngine WitnessDAO",
        "version": "1",
        "chainId": int(data["chain_id"]),
        "verifyingContract": to_checksum_address(data["verifying_contract"]),
    }


def require_bytes32(value: str, name: str) -> str:
    if not BYTES32.fullmatch(value):
        raise LoveEngineError("invalid_bytes32", name)
    return value.lower()


def build_register_typed_data(data: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(data)
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            "Register": [
                {"name": "witness", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "Register",
        "domain": domain(data),
        "message": {
            "witness": to_checksum_address(data["witness"]),
            "nonce": int(data["nonce"]),
            "deadline": int(data["deadline"]),
        },
    }


def build_vote_typed_data(data: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(data)
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            "Vote": [
                {"name": "witness", "type": "address"},
                {"name": "proposalId", "type": "uint256"},
                {"name": "support", "type": "bool"},
                {"name": "reasonHash", "type": "bytes32"},
                {"name": "payloadHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "Vote",
        "domain": domain(data),
        "message": {
            "witness": to_checksum_address(data["witness"]),
            "proposalId": int(data["proposal_id"]),
            "support": bool(data["support"]),
            "reasonHash": require_bytes32(data["reason_hash"], "reason_hash"),
            "payloadHash": require_bytes32(data["payload_hash"], "payload_hash"),
            "nonce": int(data["nonce"]),
            "deadline": int(data["deadline"]),
        },
    }
