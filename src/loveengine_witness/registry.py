"""SkillRegistry release verification and transaction plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address
from web3 import HTTPProvider, Web3

from .errors import LoveEngineError
from .hashes import keccak256_hex
from .network_typed_data import id_hash
from .schema import validate_schema


ACCEPTED_RELEASE_STATUSES = {"active"}
ACTIVE_RELEASE_STATUS = 1
SKILL_REGISTRY_READ_ABI = [
    {
        "type": "function",
        "name": "getRelease",
        "stateMutability": "view",
        "inputs": [
            {"name": "publisher", "type": "address"},
            {"name": "skillId", "type": "bytes32"},
            {"name": "versionHash", "type": "bytes32"},
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "packageHash", "type": "bytes32"},
                    {"name": "manifestHash", "type": "bytes32"},
                    {"name": "previousVersionHash", "type": "bytes32"},
                    {"name": "replacementVersionHash", "type": "bytes32"},
                    {"name": "status", "type": "uint8"},
                    {"name": "publishedAt", "type": "uint64"},
                ],
            }
        ],
    }
]


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
        "trust_bound": False,
        "verification_level": "release_file_consistency",
        "publisher": release["publisher"],
        "skill_id": release["skill_id"],
        "version": release["version"],
        "package_hash": actual,
        "status": release["status"],
    }


def verify_onchain_release(
    artifact: Path,
    *,
    rpc_url: str,
    expected_chain_id: str,
    registry: str,
    publisher: str,
    skill_id: str,
    version: str,
    web3: Any | None = None,
) -> dict[str, Any]:
    """Verify a package against an active SkillRegistry release using read-only RPC."""

    try:
        registry_address = to_checksum_address(registry)
        publisher_address = to_checksum_address(publisher)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("invalid_registry_query", str(exc)) from exc
    if not skill_id or not version:
        raise LoveEngineError("invalid_registry_query", "skill_id and version are required")

    chain = web3 or Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
    try:
        if web3 is None and not chain.is_connected():
            raise LoveEngineError("rpc_unavailable", rpc_url, 3)
        actual_chain_id = str(chain.eth.chain_id)
        if actual_chain_id != str(expected_chain_id):
            raise LoveEngineError("wrong_chain_id", "RPC chainId mismatch")
        if not bytes(chain.eth.get_code(registry_address)):
            raise LoveEngineError(
                "registry_contract_not_found", registry_address, 3
            )
        contract = chain.eth.contract(
            address=registry_address, abi=SKILL_REGISTRY_READ_ABI
        )
        raw_release = contract.functions.getRelease(
            publisher_address,
            id_hash(skill_id),
            id_hash(version),
        ).call()
    except LoveEngineError:
        raise
    except Exception as exc:
        raise LoveEngineError("registry_query_failed", str(exc), 3) from exc

    if not isinstance(raw_release, (list, tuple)) or len(raw_release) != 6:
        raise LoveEngineError("registry_query_failed", "invalid getRelease response", 3)
    try:
        package_hash = Web3.to_hex(raw_release[0]).lower()
        manifest_hash = Web3.to_hex(raw_release[1]).lower()
        status = int(raw_release[4])
        published_at = int(raw_release[5])
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("registry_query_failed", "invalid release fields", 3) from exc
    if package_hash == "0x" + "0" * 64 or manifest_hash == "0x" + "0" * 64:
        raise LoveEngineError("release_not_found", f"{skill_id}@{version}")
    if status != ACTIVE_RELEASE_STATUS:
        raise LoveEngineError("release_not_active", f"release status is {status}")
    if published_at <= 0:
        raise LoveEngineError("registry_query_failed", "active release has no timestamp", 3)

    # Import locally to keep the legacy release-file verifier independent.
    from .package import verify_package

    package = verify_package(
        artifact,
        expected_package_hash=package_hash,
    )
    if package["manifest_keccak256"].lower() != manifest_hash:
        raise LoveEngineError(
            "manifest_hash_mismatch", package["manifest_keccak256"]
        )
    if package["skill_id"] != skill_id or package["version"] != version:
        raise LoveEngineError(
            "release_metadata_mismatch", f"{package['skill_id']}@{package['version']}"
        )
    return {
        "valid": True,
        "chain_verified": True,
        "trust_bound": False,
        "verification_level": "chain_consistency",
        "chain_id": actual_chain_id,
        "registry": registry_address,
        "publisher": publisher_address,
        "skill_id": skill_id,
        "version": version,
        "package_hash": package_hash,
        "manifest_hash": manifest_hash,
        "status": "active",
        "published_at": str(published_at),
        "checked_file_count": package["checked_file_count"],
    }


# Explicit alias for adapters that name the trust root rather than the transport.
verify_registry_release_onchain = verify_onchain_release
