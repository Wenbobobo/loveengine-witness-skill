"""Verification for the release-to-ProposalGate Witness core transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .network_typed_data import payload_hash
from .participant_attestation import verify_participant_attestation_set
from .pilot_transcript import (
    verify_release_anchor_rpc,
    verify_transcript_trust_policy,
    verify_witness_evidence_stages,
)
from .rpc_endpoints import validate_rpc_pair
from .schema import validate_schema
from .secrets import reject_secret_fields
from .trust_policy import load_node_trust_policy, validate_node_trust_policy


TrustPolicyInput = dict[str, Any] | str | Path
CORE_TRANSCRIPT_V1 = "loveengine.witness-core-transcript/1"
CORE_TRANSCRIPT_V2 = "loveengine.witness-core-transcript/2"
PUBLISH_RELEASE_SELECTOR = Web3.to_hex(
    Web3.keccak(
        text="publishRelease(bytes32,bytes32,bytes32,bytes32,bytes32)"
    )[:4]
).lower()


def _expected_publish_release_calldata(value: dict[str, Any]) -> str:
    release = value["release_anchor"]
    fields = (
        Web3.to_hex(Web3.keccak(text=release["skill_id"])),
        release["version_hash"],
        release["package_hash"],
        release["manifest_hash"],
        release["previous_version_hash"],
    )
    return PUBLISH_RELEASE_SELECTOR + "".join(
        field.lower().removeprefix("0x") for field in fields
    )


def core_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def _load_policy(value: TrustPolicyInput | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return validate_node_trust_policy(value)
    return load_node_trust_policy(Path(value))


def public_service_config_hash(value: dict[str, Any]) -> str:
    """Hash the canonical, secret-free public service configuration."""

    view = dict(value)
    view.pop("config_hash", None)
    reject_secret_fields(view)
    return payload_hash(view)


def select_verification_anchor(w3: Any, *, environment: str) -> dict[str, str]:
    """Select an immutable anchor; public Sepolia runs fail closed without safe."""

    if environment == "sepolia_invited_pilot":
        try:
            if str(w3.eth.chain_id) != "11155111":
                raise LoveEngineError(
                    "wrong_chain_id", "Sepolia verification anchor requires 11155111"
                )
        except LoveEngineError:
            raise
        except Exception as exc:
            raise LoveEngineError(
                "verification_anchor_unavailable", "Sepolia chain ID", 4
            ) from exc
        selected_via = "safe"
    elif environment == "local_anvil":
        selected_via = "latest"
    else:
        raise LoveEngineError("unsupported_pilot_environment", str(environment))
    try:
        block = w3.eth.get_block(selected_via)
        return {
            "block_number": str(block["number"]),
            "block_hash": Web3.to_hex(block["hash"]),
            "timestamp": str(block["timestamp"]),
            "selected_via": selected_via,
        }
    except Exception as exc:
        raise LoveEngineError(
            "verification_anchor_unavailable",
            f"{environment}:{selected_via}",
            4,
        ) from exc


def _verification_result(
    value: dict[str, Any],
    *,
    rpc_url: str | None,
    policy: dict[str, Any] | None,
    chain_consistency_checked: bool | None = None,
) -> dict[str, Any]:
    v2_result = chain_consistency_checked is not None
    consistency = bool(rpc_url) if not v2_result else chain_consistency_checked
    trust_bound = bool(consistency and policy is not None)
    verification_level = (
        "chain_verified"
        if trust_bound
        else "chain_consistency"
        if consistency
        else "offline_integrity"
    )
    result = {
        "valid": True,
        "verification_level": verification_level,
        "chain_verified": trust_bound if v2_result else bool(rpc_url),
        "trust_bound": trust_bound,
        "run_id": value["run_id"],
        "environment": value["environment"],
        "actors_simulated": value["actors_simulated"],
        "event_count": len(value["events"]),
        "observation_receipts": len(value["observation_receipts"]),
        "review_receipts": len(value["review_receipts"]),
        "gate_ready": bool(value["proposal_gate"].get("ready")),
    }
    if v2_result:
        result["chain_consistency_checked"] = consistency
    return result


def _verify_v1(
    value: dict[str, Any],
    rpc_url: str | None,
    trust_policy: TrustPolicyInput | None,
) -> dict[str, Any]:
    validate_schema(value, "witness-core-transcript-v1.schema.json")
    if value["transcript_hash"] != core_transcript_hash(value):
        raise LoveEngineError(
            "transcript_hash_mismatch", "WitnessCoreTranscriptV1"
        )
    policy = _load_policy(trust_policy)
    verify_witness_evidence_stages(
        value,
        allowed_issuers=(policy["allowed_issuers"] if policy is not None else None),
    )
    if policy is not None:
        verify_transcript_trust_policy(value, policy)
    if rpc_url:
        verify_release_anchor_rpc(value, rpc_url)
    return _verification_result(value, rpc_url=rpc_url, policy=policy)


def _verify_signer_evidence(
    value: dict[str, Any],
    *,
    participant_nodes: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    evidence: dict[str, dict[str, Any]] = {}
    for item in value["signer_evidence"]:
        address = to_checksum_address(item["address"])
        if address in evidence:
            raise LoveEngineError("duplicate_signer_evidence", address)
        evidence[address] = item

    required: dict[str, set[str]] = {}

    def require(address: str, role: str) -> None:
        required.setdefault(to_checksum_address(address), set()).add(role)

    require(value["chain"]["publisher"], "publisher")
    for task in value["network_tasks"]:
        require(task["issuer"], "task_issuer")
    for node in participant_nodes:
        require(node, "node")
    for address, roles in required.items():
        item = evidence.get(address)
        if item is None or not roles.issubset(set(item["roles"])):
            raise LoveEngineError("signer_evidence_missing", address)
    return (
        {
            node: evidence[node]["ruleset_sha256"]
            for node in participant_nodes
        },
        {
            node: evidence[node]["rules_attestation_sha256"]
            for node in participant_nodes
        },
    )


def _item_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value[field]
    return getattr(value, field)


def observe_release_anchor_rpc(
    value: dict[str, Any], rpc_url: str, *, web3: Any | None = None
) -> str:
    """Hash one provider's canonical view of the declared historical anchor."""

    w3 = web3 or Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    w3, anchor_number = verify_release_anchor_rpc(value, rpc_url, web3=w3)
    chain = value["chain"]
    anchor = value["verification_anchor"]
    try:
        if value["environment"] == "sepolia_invited_pilot":
            safe = w3.eth.get_block("safe")
            if int(_item_field(safe, "number")) < anchor_number:
                raise LoveEngineError(
                    "verification_anchor_unavailable",
                    "RPC safe head is behind the transcript anchor",
                    4,
                )
        code = w3.eth.get_code(
            to_checksum_address(chain["registry"]),
            block_identifier=anchor_number,
        )
        code_hash = Web3.to_hex(Web3.keccak(code)).lower()
        if code_hash != chain["runtime_code_hash"].lower():
            raise LoveEngineError("registry_code_hash_mismatch", code_hash)

        receipts: dict[str, dict[str, str]] = {}
        for action, transaction_field in (
            ("deploy", "deployment_transaction"),
            ("publish", "release_transaction"),
        ):
            transaction_hash = chain[transaction_field]
            receipt = w3.eth.get_transaction_receipt(transaction_hash)
            transaction = w3.eth.get_transaction(transaction_hash)
            block_number = int(_item_field(receipt, "blockNumber"))
            block_hash = Web3.to_hex(_item_field(receipt, "blockHash")).lower()
            if int(_item_field(receipt, "status")) != 1 or block_number > anchor_number:
                raise LoveEngineError(
                    "chain_transaction_invalid", f"{action} receipt"
                )
            w3.eth.get_block(_item_field(receipt, "blockHash"))
            if to_checksum_address(_item_field(transaction, "from")) != to_checksum_address(
                chain["publisher"]
            ):
                raise LoveEngineError(
                    "chain_transaction_invalid", f"{action} sender"
                )
            target = _item_field(transaction, "to")
            if action == "deploy":
                if target is not None or to_checksum_address(
                    _item_field(receipt, "contractAddress")
                ) != to_checksum_address(chain["registry"]):
                    raise LoveEngineError(
                        "chain_transaction_invalid", "deployment target"
                    )
            else:
                if target is None or to_checksum_address(target) != to_checksum_address(
                    chain["registry"]
                ):
                    raise LoveEngineError(
                        "chain_transaction_invalid", "publish target"
                    )
                raw_input = _item_field(transaction, "input")
                call_data = (
                    raw_input.lower()
                    if isinstance(raw_input, str)
                    else Web3.to_hex(raw_input).lower()
                )
                if call_data != _expected_publish_release_calldata(value):
                    raise LoveEngineError(
                        "chain_transaction_invalid", "publish calldata"
                    )
            receipts[action] = {
                "transaction_hash": transaction_hash.lower(),
                "block_number": str(block_number),
                "block_hash": block_hash,
                "status": "1",
            }
        observation = {
            "schema_version": "loveengine.rpc-release-observation/1",
            "chain_id": chain["chain_id"],
            "registry": to_checksum_address(chain["registry"]),
            "publisher": to_checksum_address(chain["publisher"]),
            "anchor": anchor,
            "runtime_code_hash": code_hash,
            "release_status": chain["release_status"],
            "package_hash": value["release_anchor"]["package_hash"].lower(),
            "manifest_hash": value["release_anchor"]["manifest_hash"].lower(),
            "transactions": receipts,
        }
        return sha256_prefixed(canonical_json_bytes(observation))
    except LoveEngineError:
        raise
    except Exception as exc:
        raise LoveEngineError(
            "chain_verification_failed", exc.__class__.__name__, 4
        ) from exc


def _verify_v2(
    value: dict[str, Any],
    rpc_url: str | None,
    secondary_rpc_url: str | None,
    trust_policy: TrustPolicyInput | None,
) -> dict[str, Any]:
    validate_schema(value, "witness-core-transcript-v2.schema.json")
    if value["transcript_hash"] != core_transcript_hash(value):
        raise LoveEngineError(
            "transcript_hash_mismatch", "WitnessCoreTranscriptV2"
        )
    if value["verified_at"] != value["verification_anchor"]["timestamp"]:
        raise LoveEngineError(
            "core_cross_reference_mismatch", "verified_at/anchor timestamp"
        )
    service_hash = public_service_config_hash(value["public_service"])
    if value["public_service"]["config_hash"].lower() != service_hash.lower():
        raise LoveEngineError("public_service_config_hash_mismatch", service_hash)

    policy = _load_policy(trust_policy)
    members, _ = verify_witness_evidence_stages(
        value,
        allowed_issuers=(policy["allowed_issuers"] if policy is not None else None),
    )
    participant_nodes = {
        to_checksum_address(item["node"])
        for item in value["observation_receipts"] + value["review_receipts"]
    }
    if not participant_nodes.issubset(set(members)):
        raise LoveEngineError(
            "unexpected_participant", "receipt node outside bootstrap"
        )
    rulesets, rules_attestations = _verify_signer_evidence(
        value,
        participant_nodes=participant_nodes,
    )
    assignments: dict[str, tuple[str, str]] = {}
    for task in value["network_tasks"]:
        if task["task_type"] != "observe_live_text":
            continue
        recipient = to_checksum_address(task["recipient"])
        if recipient in assignments:
            raise LoveEngineError("duplicate_participant_assignment", recipient)
        assignments[recipient] = (task["task_id"], task["payload_hash"])
    if set(assignments) != participant_nodes:
        raise LoveEngineError(
            "participant_assignment_missing", "one observation task per participant"
        )
    diversity = verify_participant_attestation_set(
        value["participant_attestations"],
        expected_nodes=participant_nodes,
        expected_chain_id=value["chain"]["chain_id"],
        expected_registry=value["chain"]["registry"],
        expected_run_id=value["run_id"],
        expected_package_hash=value["release_anchor"]["package_hash"],
        expected_manifest_hash=value["release_anchor"]["manifest_hash"],
        expected_service_config_hash=service_hash,
        expected_rulesets=rulesets,
        expected_profile_hashes={
            to_checksum_address(item["profile"]["node"]): payload_hash(
                item["profile"]
            )
            for item in value["bootstrap"]["directory"]
        },
        expected_assignments=assignments,
        expected_pilot_invite_hash=value["pilot_invite_hash"],
        expected_trust_policy_hash=value["trust_policy_hash"],
        expected_rules_attestations=rules_attestations,
        now=int(value["verified_at"]),
    )
    if policy is not None:
        verify_transcript_trust_policy(value, policy)
    if bool(rpc_url) != bool(secondary_rpc_url):
        raise LoveEngineError(
            "two_rpc_endpoints_required",
            "V2 chain verification requires two RPC endpoints",
        )
    chain_consistency_checked = False
    if rpc_url and secondary_rpc_url:
        validate_rpc_pair(
            rpc_url,
            secondary_rpc_url,
            chain_id=value["chain"]["chain_id"],
            require_secondary=True,
        )
        primary = observe_release_anchor_rpc(value, rpc_url)
        secondary = observe_release_anchor_rpc(value, secondary_rpc_url)
        if primary != value["chain"]["primary_rpc_observation_hash"]:
            raise LoveEngineError("rpc_observation_hash_mismatch", "primary")
        if secondary != value["chain"]["secondary_rpc_observation_hash"]:
            raise LoveEngineError("rpc_observation_hash_mismatch", "secondary")
        if primary != secondary:
            raise LoveEngineError("rpc_observation_disagreement", "canonical state")
        chain_consistency_checked = True
    result = _verification_result(
        value,
        rpc_url=rpc_url,
        policy=policy,
        chain_consistency_checked=chain_consistency_checked,
    )
    result.update(
        {
            "input_mode": value["input_mode"],
            "participant_claims_verified": True,
            # Signatures bind addresses and claims, but not the declared Clef
            # backend, rules, or audit-log hashes in the current schema.
            "signer_backend_evidence_verified": False,
            **diversity,
        }
    )
    return result


def verify_core_transcript(
    value: dict[str, Any],
    rpc_url: str | None = None,
    secondary_rpc_url: str | None = None,
    trust_policy: TrustPolicyInput | None = None,
) -> dict[str, Any]:
    """Verify core integrity, optional chain consistency, and optional trust binding."""

    reject_secret_fields(value)
    schema_version = value.get("schema_version")
    if schema_version == CORE_TRANSCRIPT_V1:
        if secondary_rpc_url is not None:
            raise LoveEngineError(
                "unsupported_argument", "V1 does not use a secondary RPC"
            )
        return _verify_v1(value, rpc_url, trust_policy)
    if schema_version == CORE_TRANSCRIPT_V2:
        return _verify_v2(value, rpc_url, secondary_rpc_url, trust_policy)
    raise LoveEngineError("unsupported_schema_version", str(schema_version))
