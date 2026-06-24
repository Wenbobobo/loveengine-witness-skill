"""Hash helpers with protocol-specific wire formats."""

from __future__ import annotations

import hashlib
from pathlib import Path

from eth_hash.auto import keccak


TEXT_SOURCE_SUFFIXES = {
    ".css",
    ".html",
    ".json",
    ".lock",
    ".md",
    ".ndjson",
    ".py",
    ".sol",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def source_bytes_for_hash(path: Path) -> bytes:
    """Return stable bytes for manifest source hashing.

    Manifest source refs are repository text artifacts. GitHub Actions checks out
    those files with LF line endings, while a Windows working tree can still
    hold CRLF bytes. Source hashes must describe the repository content rather
    than the local checkout accident, so text refs are normalized to LF before
    hashing.
    """

    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SOURCE_SUFFIXES:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def source_sha256_prefixed(path: Path) -> str:
    return sha256_prefixed(source_bytes_for_hash(path))


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()
