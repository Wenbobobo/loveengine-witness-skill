"""Deterministic public fixtures without secret material."""

from __future__ import annotations

from pathlib import Path

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .jsonio import write_json
from .node_profile import build_node_profile


def generate_witness_fixtures(count: int, output: Path) -> list[Path]:
    if count < 1 or count > 1000:
        raise LoveEngineError("invalid_witness_count", "witnesses must be 1..1000")

    paths: list[Path] = []
    for index in range(1, count + 1):
        address = to_checksum_address("0x" + index.to_bytes(20, "big").hex())
        profile = build_node_profile(
            {
                "node_id": f"local-witness-{index:03d}",
                "agent_runtime": "local-fixture",
                "address": address,
                "signer_type": "anvil-test",
                "capabilities": [
                    "manifest_verification",
                    "evidence_hashing",
                    "vote_signing",
                ],
            }
        )
        path = output / f"witness-{index:03d}.json"
        write_json(path, profile)
        paths.append(path)
    return paths
