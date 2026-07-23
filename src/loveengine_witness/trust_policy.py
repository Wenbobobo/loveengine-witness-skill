"""Trusted out-of-band release anchors for public Agent nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .jsonio import read_json
from .schema import validate_schema


SCHEMA_VERSION = "loveengine.node-trust-policy/1"
ZERO_ADDRESS = "0x" + "00" * 20


def _address(value: str, field: str) -> str:
    try:
        address = to_checksum_address(value)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("invalid_trust_policy", f"invalid {field}") from exc
    if address.lower() == ZERO_ADDRESS:
        raise LoveEngineError("invalid_trust_policy", f"{field} cannot be zero")
    return address


def validate_node_trust_policy(value: dict[str, Any]) -> dict[str, Any]:
    """Validate policy shape and semantic invariants without changing its fields."""

    validate_schema(value, "node-trust-policy-v1.schema.json")
    _address(value["registry"], "registry")
    publisher = _address(value["publisher"], "publisher")
    issuers = [
        _address(raw, f"allowed_issuers[{index}]")
        for index, raw in enumerate(value["allowed_issuers"])
    ]
    lowered = [issuer.lower() for issuer in issuers]
    if len(lowered) != len(set(lowered)):
        raise LoveEngineError(
            "invalid_trust_policy", "allowed_issuers contains duplicate addresses"
        )
    if publisher.lower() not in lowered:
        raise LoveEngineError(
            "invalid_trust_policy", "publisher must be an allowed task issuer"
        )
    return value


def load_node_trust_policy(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return validate_node_trust_policy(value)


def build_node_trust_policy(
    *,
    chain_id: str,
    registry: str,
    publisher: str,
    skill_id: str,
    version: str,
    package_hash: str,
    manifest_hash: str,
    allowed_issuers: Iterable[str],
) -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "chain_id": str(chain_id),
        "registry": _address(registry, "registry"),
        "publisher": _address(publisher, "publisher"),
        "skill_id": skill_id,
        "version": version,
        "package_hash": package_hash.lower(),
        "manifest_hash": manifest_hash.lower(),
        "allowed_issuers": [
            _address(raw, f"allowed_issuers[{index}]")
            for index, raw in enumerate(allowed_issuers)
        ],
    }
    return validate_node_trust_policy(value)


def _same_address(left: str, right: str) -> bool:
    return to_checksum_address(left) == to_checksum_address(right)


def _require_match(field: str, actual: str, expected: str) -> None:
    if field in {"registry", "publisher"}:
        matches = _same_address(actual, expected)
    elif field in {"package_hash", "manifest_hash"}:
        matches = actual.lower() == expected.lower()
    else:
        matches = str(actual) == str(expected)
    if not matches:
        raise LoveEngineError(
            "trust_policy_mismatch", f"{field} does not match the trusted policy"
        )


def verify_invite_against_policy(
    invite: dict[str, Any], policy: dict[str, Any]
) -> None:
    for field in (
        "chain_id",
        "registry",
        "publisher",
        "skill_id",
        "version",
        "package_hash",
    ):
        _require_match(field, invite[field], policy[field])


def verify_profile_against_policy(
    signed_profile: dict[str, Any], policy: dict[str, Any]
) -> None:
    _require_match("chain_id", signed_profile["chain_id"], policy["chain_id"])
    _require_match("registry", signed_profile["registry"], policy["registry"])


def verify_release_against_policy(
    release: dict[str, Any], policy: dict[str, Any]
) -> None:
    for field in (
        "chain_id",
        "registry",
        "publisher",
        "skill_id",
        "version",
        "package_hash",
        "manifest_hash",
    ):
        _require_match(field, release[field], policy[field])
