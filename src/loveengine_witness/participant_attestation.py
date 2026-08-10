"""Signed, run-bound participant diversity declarations for invited pilots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from time import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .network_typed_data import id_hash
from .schema import validate_schema
from .secrets import reject_secret_fields
from .typed_data import DOMAIN_TYPES, require_bytes32


SCHEMA_VERSION = "loveengine.participant-attestation/1"
MAX_ATTESTATION_TTL_SECONDS = 3_600


def _domain(chain_id: str, registry: str) -> dict[str, Any]:
    return {
        "name": "LoveEngine Agent Network",
        "version": "2",
        "chainId": int(chain_id),
        "verifyingContract": to_checksum_address(registry),
    }


def _sha256_as_bytes32(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise LoveEngineError("invalid_hash", label)
    return require_bytes32("0x" + value.removeprefix("sha256:"), label)


def build_participant_attestation(
    *,
    chain_id: str,
    registry: str,
    run_id: str,
    node: str,
    role: str,
    profile_hash: str,
    assignment_task_id: str,
    assignment_payload_hash: str,
    pilot_invite_hash: str,
    trust_policy_hash: str,
    package_hash: str,
    manifest_hash: str,
    service_config_hash: str,
    ruleset_sha256: str,
    rules_attestation_sha256: str,
    operator_group_hash: str,
    network_group_hash: str,
    issued_at: str,
    valid_until: str,
) -> dict[str, Any]:
    """Build an unsigned declaration; a signer client fills ``signature``."""

    if not isinstance(run_id, str) or not run_id or not assignment_task_id:
        raise LoveEngineError("invalid_participant_attestation", "run_id")
    if int(issued_at) > int(valid_until):
        raise LoveEngineError("invalid_participant_attestation", "validity window")
    if int(valid_until) > int(issued_at) + MAX_ATTESTATION_TTL_SECONDS:
        raise LoveEngineError("invalid_participant_attestation", "validity window")
    if role != "observation_node":
        raise LoveEngineError("invalid_participant_attestation", "role")
    value = {
        "schema_version": SCHEMA_VERSION,
        "chain_id": str(chain_id),
        "registry": to_checksum_address(registry),
        "run_id": run_id,
        "node": to_checksum_address(node),
        "role": role,
        "profile_hash": require_bytes32(profile_hash, "profile_hash").lower(),
        "assignment_task_id": assignment_task_id,
        "assignment_payload_hash": require_bytes32(
            assignment_payload_hash, "assignment_payload_hash"
        ).lower(),
        "pilot_invite_hash": pilot_invite_hash,
        "trust_policy_hash": trust_policy_hash,
        "package_hash": require_bytes32(package_hash, "package_hash").lower(),
        "manifest_hash": require_bytes32(manifest_hash, "manifest_hash").lower(),
        "service_config_hash": require_bytes32(
            service_config_hash, "service_config_hash"
        ).lower(),
        "ruleset_sha256": ruleset_sha256,
        "rules_attestation_sha256": rules_attestation_sha256,
        "operator_group_hash": require_bytes32(
            operator_group_hash, "operator_group_hash"
        ).lower(),
        "network_group_hash": require_bytes32(
            network_group_hash, "network_group_hash"
        ).lower(),
        "issued_at": str(issued_at),
        "valid_until": str(valid_until),
        "signature": "0x",
    }
    _sha256_as_bytes32(ruleset_sha256, "ruleset_sha256")
    _sha256_as_bytes32(rules_attestation_sha256, "rules_attestation_sha256")
    _sha256_as_bytes32(pilot_invite_hash, "pilot_invite_hash")
    _sha256_as_bytes32(trust_policy_hash, "trust_policy_hash")
    reject_secret_fields(value)
    return value


def build_participant_attestation_typed_data(
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": DOMAIN_TYPES,
            "ParticipantAttestationV1": [
                {"name": "runId", "type": "bytes32"},
                {"name": "node", "type": "address"},
                {"name": "role", "type": "bytes32"},
                {"name": "profileHash", "type": "bytes32"},
                {"name": "assignmentTaskId", "type": "bytes32"},
                {"name": "assignmentPayloadHash", "type": "bytes32"},
                {"name": "pilotInviteHash", "type": "bytes32"},
                {"name": "trustPolicyHash", "type": "bytes32"},
                {"name": "packageHash", "type": "bytes32"},
                {"name": "manifestHash", "type": "bytes32"},
                {"name": "serviceConfigHash", "type": "bytes32"},
                {"name": "rulesetSha256", "type": "bytes32"},
                {"name": "rulesAttestationSha256", "type": "bytes32"},
                {"name": "operatorGroupHash", "type": "bytes32"},
                {"name": "networkGroupHash", "type": "bytes32"},
                {"name": "issuedAt", "type": "uint256"},
                {"name": "validUntil", "type": "uint256"},
            ],
        },
        "primaryType": "ParticipantAttestationV1",
        "domain": _domain(value["chain_id"], value["registry"]),
        "message": {
            "runId": id_hash(value["run_id"]),
            "node": to_checksum_address(value["node"]),
            "role": id_hash(value["role"]),
            "profileHash": require_bytes32(value["profile_hash"], "profile_hash"),
            "assignmentTaskId": id_hash(value["assignment_task_id"]),
            "assignmentPayloadHash": require_bytes32(
                value["assignment_payload_hash"], "assignment_payload_hash"
            ),
            "pilotInviteHash": _sha256_as_bytes32(
                value["pilot_invite_hash"], "pilot_invite_hash"
            ),
            "trustPolicyHash": _sha256_as_bytes32(
                value["trust_policy_hash"], "trust_policy_hash"
            ),
            "packageHash": require_bytes32(value["package_hash"], "package_hash"),
            "manifestHash": require_bytes32(
                value["manifest_hash"], "manifest_hash"
            ),
            "serviceConfigHash": require_bytes32(
                value["service_config_hash"], "service_config_hash"
            ),
            "rulesetSha256": _sha256_as_bytes32(
                value["ruleset_sha256"], "ruleset_sha256"
            ),
            "rulesAttestationSha256": _sha256_as_bytes32(
                value["rules_attestation_sha256"], "rules_attestation_sha256"
            ),
            "operatorGroupHash": require_bytes32(
                value["operator_group_hash"], "operator_group_hash"
            ),
            "networkGroupHash": require_bytes32(
                value["network_group_hash"], "network_group_hash"
            ),
            "issuedAt": int(value["issued_at"]),
            "validUntil": int(value["valid_until"]),
        },
    }


def _same(left: Any, right: Any, label: str) -> None:
    if left != right:
        raise LoveEngineError("participant_attestation_mismatch", label)


def verify_participant_attestation(
    value: dict[str, Any],
    *,
    expected_chain_id: str,
    expected_registry: str,
    expected_run_id: str,
    expected_role: str = "observation_node",
    expected_profile_hash: str | None = None,
    expected_assignment_task_id: str | None = None,
    expected_assignment_payload_hash: str | None = None,
    expected_pilot_invite_hash: str | None = None,
    expected_trust_policy_hash: str | None = None,
    expected_package_hash: str,
    expected_manifest_hash: str,
    expected_service_config_hash: str,
    expected_rules_attestation_sha256: str | None = None,
    now: int | None = None,
) -> str:
    reject_secret_fields(value)
    validate_schema(value, "participant-attestation-v1.schema.json")
    _same(value["chain_id"], str(expected_chain_id), "chain_id")
    _same(
        to_checksum_address(value["registry"]),
        to_checksum_address(expected_registry),
        "registry",
    )
    _same(value["run_id"], expected_run_id, "run_id")
    _same(value["role"], expected_role, "role")
    if expected_profile_hash is not None:
        _same(
            value["profile_hash"].lower(),
            expected_profile_hash.lower(),
            "profile_hash",
        )
    if expected_assignment_task_id is not None:
        _same(
            value["assignment_task_id"],
            expected_assignment_task_id,
            "assignment_task_id",
        )
    if expected_assignment_payload_hash is not None:
        _same(
            value["assignment_payload_hash"].lower(),
            expected_assignment_payload_hash.lower(),
            "assignment_payload_hash",
        )
    if expected_pilot_invite_hash is not None:
        _same(
            value["pilot_invite_hash"],
            expected_pilot_invite_hash,
            "pilot_invite_hash",
        )
    if expected_trust_policy_hash is not None:
        _same(
            value["trust_policy_hash"],
            expected_trust_policy_hash,
            "trust_policy_hash",
        )
    _same(value["package_hash"].lower(), expected_package_hash.lower(), "package_hash")
    _same(
        value["manifest_hash"].lower(), expected_manifest_hash.lower(), "manifest_hash"
    )
    if expected_rules_attestation_sha256 is not None:
        _same(
            value["rules_attestation_sha256"],
            expected_rules_attestation_sha256,
            "rules_attestation_sha256",
        )
    _same(
        value["service_config_hash"].lower(),
        expected_service_config_hash.lower(),
        "service_config_hash",
    )
    current = int(time()) if now is None else int(now)
    if int(value["issued_at"]) > current:
        raise LoveEngineError("participant_attestation_not_yet_valid", value["node"])
    if int(value["valid_until"]) < current:
        raise LoveEngineError("participant_attestation_expired", value["node"])
    if int(value["valid_until"]) > int(value["issued_at"]) + MAX_ATTESTATION_TTL_SECONDS:
        raise LoveEngineError("participant_attestation_ttl_exceeded", value["node"])
    try:
        signer = Account.recover_message(
            encode_typed_data(
                full_message=build_participant_attestation_typed_data(value)
            ),
            signature=value["signature"],
        )
    except (BadSignature, TypeError, ValueError) as exc:
        raise LoveEngineError(
            "invalid_signature", "participant attestation signature"
        ) from exc
    node = to_checksum_address(value["node"])
    if to_checksum_address(signer) != node:
        raise LoveEngineError(
            "invalid_signature", "participant attestation signer"
        )
    return node


def verify_participant_attestation_set(
    attestations: list[dict[str, Any]],
    *,
    expected_nodes: Iterable[str],
    expected_chain_id: str,
    expected_registry: str,
    expected_run_id: str,
    expected_package_hash: str,
    expected_manifest_hash: str,
    expected_service_config_hash: str,
    expected_rulesets: Mapping[str, str],
    expected_profile_hashes: Mapping[str, str],
    expected_assignments: Mapping[str, tuple[str, str]],
    expected_pilot_invite_hash: str,
    expected_trust_policy_hash: str,
    expected_rules_attestations: Mapping[str, str],
    now: int,
) -> dict[str, int]:
    """Verify signed declarations and count declared, not proven, diversity."""

    nodes = {to_checksum_address(item) for item in expected_nodes}
    if len(nodes) < 3:
        raise LoveEngineError("participant_quorum_missing", "three nodes required")
    recovered: set[str] = set()
    operator_groups: set[str] = set()
    network_groups: set[str] = set()
    normalized_rulesets = {
        to_checksum_address(address): digest
        for address, digest in expected_rulesets.items()
    }
    normalized_profiles = {
        to_checksum_address(address): digest
        for address, digest in expected_profile_hashes.items()
    }
    normalized_assignments = {
        to_checksum_address(address): assignment
        for address, assignment in expected_assignments.items()
    }
    normalized_rules_attestations = {
        to_checksum_address(address): digest
        for address, digest in expected_rules_attestations.items()
    }
    for value in attestations:
        node = verify_participant_attestation(
            value,
            expected_chain_id=expected_chain_id,
            expected_registry=expected_registry,
            expected_run_id=expected_run_id,
            expected_profile_hash=normalized_profiles.get(
                to_checksum_address(value["node"])
            ),
            expected_assignment_task_id=normalized_assignments[
                to_checksum_address(value["node"])
            ][0],
            expected_assignment_payload_hash=normalized_assignments[
                to_checksum_address(value["node"])
            ][1],
            expected_pilot_invite_hash=expected_pilot_invite_hash,
            expected_trust_policy_hash=expected_trust_policy_hash,
            expected_package_hash=expected_package_hash,
            expected_manifest_hash=expected_manifest_hash,
            expected_service_config_hash=expected_service_config_hash,
            expected_rules_attestation_sha256=normalized_rules_attestations[
                to_checksum_address(value["node"])
            ],
            now=now,
        )
        if node in recovered:
            raise LoveEngineError("duplicate_participant_attestation", node)
        if node not in nodes:
            raise LoveEngineError("unexpected_participant", node)
        if node not in normalized_profiles:
            raise LoveEngineError("participant_profile_missing", node)
        _same(
            value["ruleset_sha256"],
            normalized_rulesets.get(node),
            "ruleset_sha256",
        )
        recovered.add(node)
        operator_groups.add(value["operator_group_hash"].lower())
        network_groups.add(value["network_group_hash"].lower())
    if recovered != nodes:
        raise LoveEngineError(
            "participant_quorum_missing", "attestations do not cover nodes"
        )
    if len(operator_groups) < 2:
        raise LoveEngineError(
            "participant_diversity_missing", "two declared operator groups required"
        )
    if len(network_groups) < 2:
        raise LoveEngineError(
            "participant_diversity_missing", "two declared network groups required"
        )
    return {
        "nodes": len(recovered),
        "declared_operator_groups": len(operator_groups),
        "declared_network_groups": len(network_groups),
    }
