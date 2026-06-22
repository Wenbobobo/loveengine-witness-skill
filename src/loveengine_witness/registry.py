"""SkillRegistry release verification and transaction plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .hashes import keccak256_hex
from .network_typed_data import id_hash
from .schema import validate_schema


ACCEPTED_RELEASE_STATUSES = {"active"}


def publish_plan(release: dict[str, Any]) -> dict[str, Any]:
    validate_schema(release, "skill-release-v1.schema.json")
    return {
        "call": "publishRelease",
        "arguments": [
            id_hash(release["skill_id"]),
            release["version_hash"],
            release["package_hash"],
            release["manifest_hash"],
            release["previous_version_hash"],
        ],
        "publisher": release["publisher"],
        "submitted": False,
    }


def verify_release(
    release: dict[str, Any],
    artifact: Path,
    *,
    expected_chain_id: str,
    expected_registry: str,
    expected_publisher: str,
) -> dict[str, Any]:
    validate_schema(release, "skill-release-v1.schema.json")
    if release["chain_id"] != str(expected_chain_id):
        raise LoveEngineError("wrong_chain_id", "release chainId mismatch")
    if to_checksum_address(release["registry"]) != to_checksum_address(
        expected_registry
    ):
        raise LoveEngineError("wrong_registry", "release Registry mismatch")
    if to_checksum_address(release["publisher"]) != to_checksum_address(
        expected_publisher
    ):
        raise LoveEngineError("wrong_publisher", "release Publisher mismatch")
    if release["status"] not in ACCEPTED_RELEASE_STATUSES:
        raise LoveEngineError(
            "release_not_active",
            f"release status is {release['status']}",
        )
    try:
        actual = keccak256_hex(artifact.read_bytes())
    except FileNotFoundError as exc:
        raise LoveEngineError("artifact_not_found", str(artifact), 3) from exc
    if actual != release["package_hash"]:
        raise LoveEngineError("package_hash_mismatch", actual)
    return {
        "valid": True,
        "publisher": release["publisher"],
        "skill_id": release["skill_id"],
        "version": release["version"],
        "package_hash": actual,
        "status": release["status"],
    }
