"""Hash helpers with protocol-specific wire formats."""

from __future__ import annotations

import hashlib

from eth_hash.auto import keccak


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()
