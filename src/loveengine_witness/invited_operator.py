"""Operator-side helpers for externally signed invited-pilot inputs."""

from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from eth_utils import to_checksum_address

from .canonical import canonical_json_bytes
from .core_transcript import public_service_config_hash
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json, write_json
from .m4_network import build_task_v2, verify_bootstrap_v2, verify_task_v2
from .m4_typed_data import build_task_v2_typed_data
from .network_typed_data import payload_hash
from .participant_attestation import (
    build_participant_attestation,
    build_participant_attestation_typed_data,
    verify_participant_attestation,
)
from .pilot_config import (
    read_pilot_write_token,
    validate_admin_origin,
    validate_pilot_invite_v2,
)
from .schema import validate_schema
from .secrets import reject_secret_fields
from .signer_client import (
    _build_verified_signer_client,
    load_external_signer_config,
)
from .trust_policy import (
    load_node_trust_policy,
    verify_invite_against_policy,
)


MAX_OPERATOR_SIGNATURE_TTL_SECONDS = 3_600


def _require_unsigned(value: dict[str, Any], label: str) -> None:
    if value.get("signature") != "0x":
        raise LoveEngineError("unsigned_input_required", label)


def _load_bootstrap_context(
    policy: dict[str, Any], bootstrap_path: Path, *, now: int
) -> tuple[dict[str, Any], dict[str, str]]:
    bootstrap = read_json(bootstrap_path)
    verify_bootstrap_v2(
        bootstrap,
        policy["chain_id"],
        policy["registry"],
        now=now,
    )
    if to_checksum_address(bootstrap["publisher"]) != to_checksum_address(
        policy["publisher"]
    ):
        raise LoveEngineError("trust_policy_mismatch", "bootstrap publisher")
    profiles = {
        to_checksum_address(item["profile"]["node"]): payload_hash(item["profile"])
        for item in bootstrap["directory"]
    }
    return bootstrap, profiles


def _verified_signer(config: Any, **evidence: Any) -> Any:
    signer, _ = _build_verified_signer_client(config, **evidence)
    return signer


def sign_network_task(
    input_path: Path,
    signer_config_path: Path,
    output_path: Path,
    *,
    trust_policy_path: Path,
    bootstrap_path: Path,
    ruleset_path: Path | None = None,
    rules_attestation_path: Path | None = None,
    clef_binary: Path | None = None,
    expected_binary_sha256: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    checked_at = int(time()) if now is None else now
    task = read_json(input_path)
    if not isinstance(task, dict):
        raise LoveEngineError("invalid_task", "task must be a JSON object")
    reject_secret_fields(task)
    _require_unsigned(task, "NetworkTaskV2")
    expected = build_task_v2(
        chain_id=task["chain_id"],
        registry=task["registry"],
        task_id=task["task_id"],
        task_type=task["task_type"],
        issuer=task["issuer"],
        recipient=task["recipient"],
        manifest_hash=task["manifest_hash"],
        payload=task["payload"],
        nonce=task["nonce"],
        deadline=task["deadline"],
    )
    if expected != task:
        raise LoveEngineError("task_not_canonical", task.get("task_id", "unknown"))
    payload_schema = {
        "observe_live_text": "observe-live-text-payload-v1.schema.json",
        "review_dispute": "review-dispute-payload-v1.schema.json",
    }[task["task_type"]]
    validate_schema(task["payload"], payload_schema)
    policy = load_node_trust_policy(trust_policy_path)
    _, profiles = _load_bootstrap_context(policy, bootstrap_path, now=checked_at)
    if task["chain_id"] != policy["chain_id"]:
        raise LoveEngineError("trust_policy_mismatch", "task chain_id")
    if to_checksum_address(task["registry"]) != to_checksum_address(
        policy["registry"]
    ):
        raise LoveEngineError("trust_policy_mismatch", "task registry")
    if task["manifest_hash"].lower() != policy["manifest_hash"].lower():
        raise LoveEngineError("trust_policy_mismatch", "task manifest_hash")
    if to_checksum_address(task["issuer"]) not in {
        to_checksum_address(item) for item in policy["allowed_issuers"]
    }:
        raise LoveEngineError("unexpected_task_issuer", task["issuer"])
    if to_checksum_address(task["recipient"]) not in profiles:
        raise LoveEngineError("unexpected_task_recipient", task["recipient"])
    if int(task["deadline"]) > checked_at + MAX_OPERATOR_SIGNATURE_TTL_SECONDS:
        raise LoveEngineError("task_ttl_exceeded", task["task_id"])
    config = load_external_signer_config(signer_config_path)
    if config.role != "task_issuer":
        raise LoveEngineError("wrong_signer_role", config.role)
    if config.chain_id != task["chain_id"]:
        raise LoveEngineError("wrong_chain_id", config.chain_id)
    if config.address != to_checksum_address(task["issuer"]):
        raise LoveEngineError("wrong_signer_address", config.address)
    signer = _verified_signer(
        config,
        ruleset_path=ruleset_path,
        rules_attestation_path=rules_attestation_path,
        binary=clef_binary,
        expected_binary_sha256=expected_binary_sha256,
    )
    task["signature"] = signer.sign_typed_data(build_task_v2_typed_data(task))
    verify_task_v2(
        task,
        expected_chain_id=task["chain_id"],
        expected_registry=task["registry"],
        expected_recipient=task["recipient"],
        expected_issuer=task["issuer"],
        expected_manifest_hash=task["manifest_hash"],
        now=checked_at,
    )
    write_json(output_path, task)
    return {
        "signed": True,
        "schema_version": task["schema_version"],
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "issuer": task["issuer"],
        "recipient": task["recipient"],
        "output": str(output_path.resolve()),
    }


def sign_participant_attestation(
    input_path: Path,
    signer_config_path: Path,
    output_path: Path,
    *,
    trust_policy_path: Path,
    bootstrap_path: Path,
    invite_path: Path,
    assignment_task_path: Path,
    service_config_path: Path,
    ruleset_path: Path | None = None,
    rules_attestation_path: Path | None = None,
    clef_binary: Path | None = None,
    expected_binary_sha256: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    checked_at = int(time()) if now is None else now
    value = read_json(input_path)
    if not isinstance(value, dict):
        raise LoveEngineError(
            "invalid_participant_attestation", "attestation must be an object"
        )
    reject_secret_fields(value)
    _require_unsigned(value, "ParticipantAttestationV1")
    expected = build_participant_attestation(
        chain_id=value["chain_id"],
        registry=value["registry"],
        run_id=value["run_id"],
        node=value["node"],
        role=value["role"],
        profile_hash=value["profile_hash"],
        assignment_task_id=value["assignment_task_id"],
        assignment_payload_hash=value["assignment_payload_hash"],
        pilot_invite_hash=value["pilot_invite_hash"],
        trust_policy_hash=value["trust_policy_hash"],
        package_hash=value["package_hash"],
        manifest_hash=value["manifest_hash"],
        service_config_hash=value["service_config_hash"],
        ruleset_sha256=value["ruleset_sha256"],
        rules_attestation_sha256=value["rules_attestation_sha256"],
        operator_group_hash=value["operator_group_hash"],
        network_group_hash=value["network_group_hash"],
        issued_at=value["issued_at"],
        valid_until=value["valid_until"],
    )
    if expected != value:
        raise LoveEngineError(
            "participant_attestation_not_canonical", value.get("run_id", "unknown")
        )
    policy = load_node_trust_policy(trust_policy_path)
    if value["chain_id"] != policy["chain_id"]:
        raise LoveEngineError("participant_attestation_mismatch", "chain_id")
    if to_checksum_address(value["registry"]) != to_checksum_address(
        policy["registry"]
    ):
        raise LoveEngineError("participant_attestation_mismatch", "registry")
    bootstrap, profiles = _load_bootstrap_context(
        policy, bootstrap_path, now=checked_at
    )
    invite = validate_pilot_invite_v2(
        read_json(invite_path), current_time=checked_at
    )
    verify_invite_against_policy(invite, policy)
    assignment = read_json(assignment_task_path)
    if to_checksum_address(assignment["issuer"]) not in {
        to_checksum_address(item) for item in policy["allowed_issuers"]
    }:
        raise LoveEngineError("unexpected_task_issuer", assignment["issuer"])
    verify_task_v2(
        assignment,
        expected_chain_id=policy["chain_id"],
        expected_registry=policy["registry"],
        expected_recipient=value["node"],
        expected_issuer=assignment["issuer"],
        expected_manifest_hash=policy["manifest_hash"],
        now=checked_at,
    )
    if assignment["task_type"] != "observe_live_text":
        raise LoveEngineError("participant_assignment_invalid", "task type")
    if assignment["payload"].get("session_id") != value["run_id"]:
        raise LoveEngineError("participant_assignment_invalid", "run_id")
    service = read_json(service_config_path)
    validate_schema(service, "invited-pilot-service-config-v1.schema.json")
    service_hash = public_service_config_hash(service)
    node = to_checksum_address(value["node"])
    expected_values = {
        "profile_hash": profiles.get(node),
        "assignment_task_id": assignment["task_id"],
        "assignment_payload_hash": assignment["payload_hash"],
        "pilot_invite_hash": sha256_prefixed(canonical_json_bytes(invite)),
        "trust_policy_hash": sha256_prefixed(canonical_json_bytes(policy)),
        "package_hash": policy["package_hash"],
        "manifest_hash": policy["manifest_hash"],
        "service_config_hash": service_hash,
    }
    for field, trusted in expected_values.items():
        if trusted is None or str(value[field]).lower() != str(trusted).lower():
            raise LoveEngineError("participant_attestation_mismatch", field)
    if int(value["valid_until"]) > min(
        int(assignment["deadline"]),
        int(bootstrap["valid_until"]),
        int(invite["expires_at"]),
        checked_at + MAX_OPERATOR_SIGNATURE_TTL_SECONDS,
    ):
        raise LoveEngineError("participant_attestation_mismatch", "valid_until")
    config = load_external_signer_config(signer_config_path)
    if config.role != "observation_node":
        raise LoveEngineError("wrong_signer_role", config.role)
    if config.chain_id != value["chain_id"]:
        raise LoveEngineError("wrong_chain_id", config.chain_id)
    if config.address != to_checksum_address(value["node"]):
        raise LoveEngineError("wrong_signer_address", config.address)
    if config.kind == "clef" and config.ruleset_sha256 != value["ruleset_sha256"]:
        raise LoveEngineError("ruleset_hash_mismatch", value["node"])
    if (
        config.kind == "clef"
        and config.rules_attestation_sha256
        != value["rules_attestation_sha256"]
    ):
        raise LoveEngineError("rules_attestation_hash_mismatch", value["node"])
    signer = _verified_signer(
        config,
        ruleset_path=ruleset_path,
        rules_attestation_path=rules_attestation_path,
        binary=clef_binary,
        expected_binary_sha256=expected_binary_sha256,
    )
    value["signature"] = signer.sign_typed_data(
        build_participant_attestation_typed_data(value)
    )
    verify_participant_attestation(
        value,
        expected_chain_id=value["chain_id"],
        expected_registry=value["registry"],
        expected_run_id=value["run_id"],
        expected_profile_hash=value["profile_hash"],
        expected_assignment_task_id=assignment["task_id"],
        expected_assignment_payload_hash=assignment["payload_hash"],
        expected_pilot_invite_hash=expected_values["pilot_invite_hash"],
        expected_trust_policy_hash=expected_values["trust_policy_hash"],
        expected_package_hash=value["package_hash"],
        expected_manifest_hash=value["manifest_hash"],
        expected_service_config_hash=value["service_config_hash"],
        expected_rules_attestation_sha256=value["rules_attestation_sha256"],
        now=checked_at,
    )
    write_json(output_path, value)
    return {
        "signed": True,
        "schema_version": value["schema_version"],
        "run_id": value["run_id"],
        "node": value["node"],
        "output": str(output_path.resolve()),
    }


def enqueue_network_task(
    input_path: Path,
    *,
    admin_url: str,
    origin: str,
    token_file: Path,
) -> dict[str, Any]:
    base = validate_admin_origin(admin_url).rstrip("/")
    exact_origin = validate_admin_origin(origin)
    task = read_json(input_path)
    reject_secret_fields(task)
    validate_schema(task, "network-task-v2.schema.json")
    token = read_pilot_write_token(token_file.resolve())
    request = Request(
        base + "/v1/relay/tasks",
        data=json.dumps(
            task,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": exact_origin,
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            status = response.status
    except HTTPError as exc:
        raise LoveEngineError("pilot_task_rejected", f"HTTP {exc.code}", 4) from exc
    except (OSError, URLError, UnicodeError, ValueError) as exc:
        raise LoveEngineError("pilot_unavailable", exc.__class__.__name__, 4) from exc
    if status != 202 or result.get("queued") is not True:
        raise LoveEngineError("pilot_task_rejected", f"HTTP {status}", 4)
    return {
        "queued": True,
        "idempotent_replay": bool(result.get("idempotent_replay", False)),
        "task_id": result["task_id"],
        "recipient": result["recipient"],
        "admin_url": base,
    }
