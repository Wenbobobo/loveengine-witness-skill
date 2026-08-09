"""External signer boundary for local Anvil and Clef-backed identities."""

from __future__ import annotations

import json
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_account.typed_transactions import TypedTransaction
from eth_utils import to_checksum_address
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json
from .network_typed_data import id_hash
from .schema import validate_schema
from .secrets import reject_secret_fields
from .typed_data import DOMAIN_TYPES


ROLE_PRIMARY_TYPES = {
    "publisher": {"BootstrapV2", "TransactionPlanAuthorizationV1"},
    "task_issuer": {"NetworkTaskV2"},
    "observation_node": {
        "NodeProfileV2",
        "TaskReceiptV2",
        "ParticipantAttestationV1",
    },
    "voting_witness": {"Vote"},
}

TYPED_DATA_FIELDS = {
    "BootstrapV2": [
        {"name": "publisher", "type": "address"},
        {"name": "directoryHash", "type": "bytes32"},
        {"name": "sequence", "type": "uint256"},
        {"name": "validUntil", "type": "uint256"},
    ],
    "NetworkTaskV2": [
        {"name": "taskId", "type": "bytes32"},
        {"name": "taskType", "type": "bytes32"},
        {"name": "issuer", "type": "address"},
        {"name": "recipient", "type": "address"},
        {"name": "manifestHash", "type": "bytes32"},
        {"name": "payloadHash", "type": "bytes32"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ],
    "NodeProfileV2": [
        {"name": "node", "type": "address"},
        {"name": "profileHash", "type": "bytes32"},
        {"name": "sequence", "type": "uint256"},
        {"name": "validUntil", "type": "uint256"},
    ],
    "TaskReceiptV2": [
        {"name": "taskId", "type": "bytes32"},
        {"name": "node", "type": "address"},
        {"name": "status", "type": "bytes32"},
        {"name": "resultHash", "type": "bytes32"},
        {"name": "nonce", "type": "uint256"},
        {"name": "completedAt", "type": "uint256"},
    ],
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
    "Vote": [
        {"name": "witness", "type": "address"},
        {"name": "proposalId", "type": "uint256"},
        {"name": "support", "type": "bool"},
        {"name": "reasonHash", "type": "bytes32"},
        {"name": "payloadHash", "type": "bytes32"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ],
    "TransactionPlanAuthorizationV1": [
        {"name": "planHash", "type": "bytes32"},
        {"name": "transactionHash", "type": "bytes32"},
        {"name": "publisher", "type": "address"},
        {"name": "expiresAt", "type": "uint256"},
    ],
}
TYPED_DATA_IDENTITY_FIELD = {
    "BootstrapV2": "publisher",
    "NetworkTaskV2": "issuer",
    "NodeProfileV2": "node",
    "TaskReceiptV2": "node",
    "ParticipantAttestationV1": "node",
    "Vote": "witness",
    "TransactionPlanAuthorizationV1": "publisher",
}
TYPED_DATA_EXPIRY_FIELD = {
    "BootstrapV2": "validUntil",
    "NetworkTaskV2": "deadline",
    "NodeProfileV2": "validUntil",
    "ParticipantAttestationV1": "validUntil",
    "Vote": "deadline",
    "TransactionPlanAuthorizationV1": "expiresAt",
}

CLEF_COMPAT_VERSION = "1.17.3"
CLEF_EXTERNAL_API_VERSION = "6.0.0"


class SignerClient(Protocol):
    address: str
    chain_id: str
    role: str

    def sign_message(self, message: str) -> str: ...

    def sign_typed_data(self, typed_data: dict[str, Any]) -> str: ...

    def sign_transaction(
        self, transaction: dict[str, Any], method_signature: str | None = None
    ) -> str: ...


@dataclass(frozen=True)
class ExternalSignerConfig:
    kind: str
    role: str
    address: str
    chain_id: str
    approval_mode: str
    request_timeout_seconds: int
    transport: str
    endpoint: str
    ruleset_sha256: str | None
    rules_attestation_sha256: str | None
    allowed_typed_data: frozenset[tuple[str, str, str, str]]
    approved_transaction_request_hashes: frozenset[str]


@dataclass(frozen=True)
class ClefRuntimeEvidence:
    ruleset_sha256: str
    rules_attestation_sha256: str
    binary_sha256: str
    binary_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": True,
            "ruleset_sha256": self.ruleset_sha256,
            "rules_attestation_sha256": self.rules_attestation_sha256,
            "binary_sha256": self.binary_sha256,
            "binary_version": self.binary_version,
        }


def _loopback_http_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1"}
        and port is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    )


def parse_external_signer_config(value: dict[str, Any]) -> ExternalSignerConfig:
    validate_schema(value, "external-signer-config-v1.schema.json")
    kind = value["kind"]
    transport = value["transport"]
    endpoint = value["endpoint"]
    chain_id = value["chain_id"]
    if transport == "http" and not _loopback_http_endpoint(endpoint):
        raise LoveEngineError("signer_endpoint_not_loopback", endpoint)
    if transport == "ipc":
        endpoint_path = Path(endpoint)
        if not endpoint_path.is_absolute():
            raise LoveEngineError("signer_ipc_path_not_absolute", endpoint)
    if kind == "anvil_rpc":
        if transport != "http" or chain_id != "31337":
            raise LoveEngineError("invalid_anvil_signer_config", chain_id)
        if value["ruleset_sha256"] is not None or value[
            "rules_attestation_sha256"
        ] is not None:
            raise LoveEngineError("invalid_anvil_signer_config", "rules hashes")
    elif value["ruleset_sha256"] is None or value[
        "rules_attestation_sha256"
    ] is None:
        raise LoveEngineError("clef_rules_attestation_required", endpoint)
    allowed = frozenset(
        (
            item["domain_name"],
            item["domain_version"],
            to_checksum_address(item["verifying_contract"]),
            item["primary_type"],
        )
        for item in value["allowed_typed_data"]
    )
    role_types = ROLE_PRIMARY_TYPES[value["role"]]
    if any(
        primary_type not in role_types
        for _, _, _, primary_type in allowed
    ):
        raise LoveEngineError("signer_role_scope_invalid", value["role"])
    approved_transaction_request_hashes = frozenset(
        value["approved_transaction_request_hashes"]
    )
    if value["role"] != "publisher" and approved_transaction_request_hashes:
        raise LoveEngineError("signer_role_scope_invalid", value["role"])
    return ExternalSignerConfig(
        kind=kind,
        role=value["role"],
        address=to_checksum_address(value["address"]),
        chain_id=chain_id,
        approval_mode=value["approval_mode"],
        request_timeout_seconds=value["request_timeout_seconds"],
        transport=transport,
        endpoint=endpoint,
        ruleset_sha256=value["ruleset_sha256"],
        rules_attestation_sha256=value["rules_attestation_sha256"],
        allowed_typed_data=allowed,
        approved_transaction_request_hashes=approved_transaction_request_hashes,
    )


def load_external_signer_config(path: Path) -> ExternalSignerConfig:
    value = read_json(path)
    if not isinstance(value, dict):
        raise LoveEngineError("signer_config_not_object", str(path))
    return parse_external_signer_config(value)


def verify_clef_evidence_files(
    config: ExternalSignerConfig,
    *,
    ruleset_path: Path,
    rules_attestation_path: Path,
) -> dict[str, Any]:
    if config.kind != "clef":
        raise LoveEngineError("wrong_signer_kind", config.kind)
    try:
        evidence = {
            "ruleset_sha256": sha256_prefixed(Path(ruleset_path).read_bytes()),
            "rules_attestation_sha256": sha256_prefixed(
                Path(rules_attestation_path).read_bytes()
            ),
        }
    except OSError as exc:
        raise LoveEngineError(
            "clef_evidence_file_unavailable", exc.__class__.__name__, 3
        ) from exc
    for field, expected in (
        ("ruleset_sha256", config.ruleset_sha256),
        ("rules_attestation_sha256", config.rules_attestation_sha256),
    ):
        if evidence[field] != expected:
            raise LoveEngineError("clef_evidence_hash_mismatch", field)
    return {"verified": True, **evidence}


def inspect_clef_binary(
    binary: Path,
    *,
    expected_sha256: str,
    expected_version: str = CLEF_COMPAT_VERSION,
) -> dict[str, Any]:
    binary = Path(binary).resolve()
    if not binary.is_file():
        raise LoveEngineError("clef_binary_missing", str(binary), 3)
    actual_sha256 = sha256_prefixed(binary.read_bytes())
    if actual_sha256 != expected_sha256:
        raise LoveEngineError("clef_binary_hash_mismatch", actual_sha256)
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LoveEngineError("clef_binary_unavailable", exc.__class__.__name__, 4) from exc
    output = (result.stdout + "\n" + result.stderr).strip()
    match = re.search(r"(?i)\b(?:clef\s+)?version[: ]+v?(\d+\.\d+\.\d+)", output)
    if result.returncode != 0 or match is None:
        raise LoveEngineError("clef_version_unrecognized", sha256_prefixed(output.encode()))
    version = match.group(1)
    if version != expected_version:
        raise LoveEngineError("clef_version_mismatch", version)
    return {
        "verified": True,
        "version": version,
        "binary_sha256": actual_sha256,
        "version_output_sha256": sha256_prefixed(output.encode()),
    }


def verify_clef_runtime_evidence(
    config: ExternalSignerConfig,
    *,
    ruleset_path: Path,
    rules_attestation_path: Path,
    binary: Path,
    expected_binary_sha256: str,
) -> ClefRuntimeEvidence:
    files = verify_clef_evidence_files(
        config,
        ruleset_path=ruleset_path,
        rules_attestation_path=rules_attestation_path,
    )
    executable = inspect_clef_binary(
        binary,
        expected_sha256=expected_binary_sha256,
    )
    return ClefRuntimeEvidence(
        ruleset_sha256=files["ruleset_sha256"],
        rules_attestation_sha256=files["rules_attestation_sha256"],
        binary_sha256=executable["binary_sha256"],
        binary_version=executable["version"],
    )


def _build_verified_signer_client(
    config: ExternalSignerConfig,
    *,
    ruleset_path: Path | None,
    rules_attestation_path: Path | None,
    binary: Path | None,
    expected_binary_sha256: str | None,
    web3: Any | None = None,
) -> tuple[SignerClient, ClefRuntimeEvidence | None]:
    supplied = (
        ruleset_path,
        rules_attestation_path,
        binary,
        expected_binary_sha256,
    )
    if config.kind == "anvil_rpc":
        if any(item is not None for item in supplied):
            raise LoveEngineError(
                "clef_runtime_evidence_not_allowed", "anvil signer"
            )
        return build_signer_client(config, web3=web3), None
    if any(item is None for item in supplied):
        raise LoveEngineError(
            "clef_runtime_evidence_required",
            "rules, attestation, binary, and binary hash are all required",
        )
    evidence = verify_clef_runtime_evidence(
        config,
        ruleset_path=ruleset_path,
        rules_attestation_path=rules_attestation_path,
        binary=binary,
        expected_binary_sha256=expected_binary_sha256,
    )
    client = _ClefSigner(config)
    client.probe()
    return client, evidence


def _validate_signature(signature: Any) -> str:
    try:
        raw = HexBytes(signature)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("signer_response_invalid", "invalid signature") from exc
    if len(raw) != 65:
        raise LoveEngineError("signer_response_invalid", "expected 65-byte signature")
    return Web3.to_hex(raw)


def _validate_typed_scope(
    config: ExternalSignerConfig, typed_data: dict[str, Any]
) -> None:
    reject_secret_fields(typed_data)
    try:
        domain = typed_data["domain"]
        primary_type = typed_data["primaryType"]
        scope = (
            domain["name"],
            domain["version"],
            to_checksum_address(domain["verifyingContract"]),
            primary_type,
        )
        chain_id = str(int(domain["chainId"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise LoveEngineError("typed_data_invalid", str(exc)) from exc
    if chain_id != config.chain_id:
        raise LoveEngineError("wrong_chain_id", chain_id)
    if scope not in config.allowed_typed_data:
        raise LoveEngineError("typed_data_not_allowed", primary_type)
    try:
        types = typed_data["types"]
        message = typed_data["message"]
        expected_fields = TYPED_DATA_FIELDS[primary_type]
        if set(types) != {"EIP712Domain", primary_type}:
            raise ValueError("type inventory")
        if types["EIP712Domain"] != DOMAIN_TYPES or types[primary_type] != expected_fields:
            raise ValueError("field definitions")
        if set(message) != {item["name"] for item in expected_fields}:
            raise ValueError("message inventory")
        identity = to_checksum_address(
            message[TYPED_DATA_IDENTITY_FIELD[primary_type]]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LoveEngineError("typed_data_invalid", str(exc)) from exc
    if identity != config.address:
        raise LoveEngineError("wrong_signer_address", identity)
    expiry_field = TYPED_DATA_EXPIRY_FIELD.get(primary_type)
    if expiry_field is not None:
        try:
            now = int(time())
            expiry = int(message[expiry_field])
            if expiry < now:
                raise LoveEngineError("typed_data_expired", primary_type)
            max_ttl = {
                "BootstrapV2": 86_400,
                "NetworkTaskV2": 3_600,
                "NodeProfileV2": 86_400,
                "ParticipantAttestationV1": 3_600,
                "Vote": 3_600,
                "TransactionPlanAuthorizationV1": 3_600,
            }[primary_type]
            if expiry > now + max_ttl:
                raise LoveEngineError("typed_data_ttl_exceeded", primary_type)
        except (TypeError, ValueError) as exc:
            raise LoveEngineError("typed_data_invalid", expiry_field) from exc
    if primary_type == "TaskReceiptV2":
        try:
            completed_at = int(message["completedAt"])
        except (TypeError, ValueError) as exc:
            raise LoveEngineError("typed_data_invalid", "completedAt") from exc
        now = int(time())
        if completed_at > now + 60 or completed_at < now - 86_400:
            raise LoveEngineError("typed_data_time_invalid", primary_type)
    _validate_typed_semantics(primary_type, message)


def _validate_typed_semantics(primary_type: str, message: dict[str, Any]) -> None:
    """Reject context-free requests that are invalid for every trusted workflow.

    Trust-policy membership and assignment checks remain the responsibility of the
    higher-level operator/session APIs. This boundary still prevents a caller from
    turning the transport adapter into a generic signer for malformed protocol data.
    """

    zero_bytes32 = "0x" + "00" * 32

    def require_nonzero(field: str) -> None:
        value = str(message[field]).lower()
        if value == zero_bytes32:
            raise LoveEngineError("typed_data_semantics_invalid", field)

    if primary_type == "NetworkTaskV2":
        if str(message["taskType"]).lower() not in {
            id_hash("observe_live_text").lower(),
            id_hash("review_dispute").lower(),
        }:
            raise LoveEngineError("typed_data_semantics_invalid", "taskType")
        if int(message["recipient"], 16) == 0:
            raise LoveEngineError("typed_data_semantics_invalid", "recipient")
        for field in ("taskId", "manifestHash", "payloadHash"):
            require_nonzero(field)
    elif primary_type == "ParticipantAttestationV1":
        if str(message["role"]).lower() != id_hash("observation_node").lower():
            raise LoveEngineError("typed_data_semantics_invalid", "role")
        if int(message["node"], 16) == 0:
            raise LoveEngineError("typed_data_semantics_invalid", "node")
        for field in (
            "runId",
            "profileHash",
            "assignmentTaskId",
            "assignmentPayloadHash",
            "pilotInviteHash",
            "trustPolicyHash",
            "packageHash",
            "manifestHash",
            "serviceConfigHash",
            "rulesetSha256",
            "rulesAttestationSha256",
            "operatorGroupHash",
            "networkGroupHash",
        ):
            require_nonzero(field)
        if int(message["issuedAt"]) > int(message["validUntil"]):
            raise LoveEngineError("typed_data_semantics_invalid", "validity window")
    elif primary_type == "NodeProfileV2":
        if int(message["node"], 16) == 0:
            raise LoveEngineError("typed_data_semantics_invalid", "node")
        require_nonzero("profileHash")
    elif primary_type == "BootstrapV2":
        if int(message["publisher"], 16) == 0:
            raise LoveEngineError("typed_data_semantics_invalid", "publisher")
        require_nonzero("directoryHash")
    elif primary_type == "TaskReceiptV2":
        if int(message["node"], 16) == 0:
            raise LoveEngineError("typed_data_semantics_invalid", "node")
        for field in ("taskId", "status", "resultHash"):
            require_nonzero(field)


TRANSACTION_FIELDS = {
    "type",
    "chainId",
    "from",
    "to",
    "gas",
    "maxFeePerGas",
    "maxPriorityFeePerGas",
    "value",
    "nonce",
    "data",
}


def _quantity(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise LoveEngineError("transaction_request_invalid", field)
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("transaction_request_invalid", field) from exc
    if parsed < 0:
        raise LoveEngineError("transaction_request_invalid", field)
    return parsed


def canonical_transaction_request(transaction: dict[str, Any]) -> dict[str, Any]:
    """Normalize the exact EIP-1559 request admitted to an external signer."""

    if set(transaction) != TRANSACTION_FIELDS:
        raise LoveEngineError("transaction_request_invalid", "field inventory")
    tx_type = _quantity(transaction["type"], "type")
    chain_id = _quantity(transaction["chainId"], "chainId")
    value = _quantity(transaction["value"], "value")
    if tx_type != 2:
        raise LoveEngineError("transaction_type_not_allowed", str(tx_type))
    if value != 0:
        raise LoveEngineError("transaction_value_not_allowed", str(value))
    try:
        sender = to_checksum_address(transaction["from"])
        recipient = (
            None
            if transaction["to"] in {None, "", "0x"}
            else to_checksum_address(transaction["to"])
        )
        data = Web3.to_hex(HexBytes(transaction["data"])).lower()
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("transaction_request_invalid", "address or data") from exc
    return {
        "type": "0x2",
        "chainId": hex(chain_id),
        "from": sender,
        "to": recipient,
        "gas": hex(_quantity(transaction["gas"], "gas")),
        "maxFeePerGas": hex(
            _quantity(transaction["maxFeePerGas"], "maxFeePerGas")
        ),
        "maxPriorityFeePerGas": hex(
            _quantity(
                transaction["maxPriorityFeePerGas"], "maxPriorityFeePerGas"
            )
        ),
        "value": "0x0",
        "nonce": hex(_quantity(transaction["nonce"], "nonce")),
        "data": data,
    }


def transaction_request_hash(transaction: dict[str, Any]) -> str:
    return sha256_prefixed(canonical_json_bytes(canonical_transaction_request(transaction)))


def _validate_transaction_scope(
    config: ExternalSignerConfig, transaction: dict[str, Any]
) -> dict[str, Any]:
    normalized = canonical_transaction_request(transaction)
    if str(int(normalized["chainId"], 0)) != config.chain_id:
        raise LoveEngineError("wrong_chain_id", normalized["chainId"])
    if normalized["from"] != config.address:
        raise LoveEngineError("wrong_signer_address", normalized["from"])
    request_hash = sha256_prefixed(canonical_json_bytes(normalized))
    if request_hash not in config.approved_transaction_request_hashes:
        raise LoveEngineError("transaction_request_not_approved", request_hash)
    return normalized


def _verify_signed_transaction(
    address: str, expected: dict[str, Any], raw_transaction: Any
) -> str:
    try:
        raw = HexBytes(raw_transaction)
        decoded = TypedTransaction.from_bytes(raw).as_dict()
        recovered = Account.recover_transaction(raw)
    except Exception as exc:
        raise LoveEngineError("signer_response_invalid", "transaction decode") from exc
    if to_checksum_address(recovered) != address:
        raise LoveEngineError("signer_address_mismatch", recovered)
    access_list = decoded.get("accessList")
    if access_list not in (None, [], ()):
        raise LoveEngineError("signed_transaction_mismatch", "accessList")
    try:
        actual = canonical_transaction_request(
            {
                "type": decoded["type"],
                "chainId": decoded["chainId"],
                "from": recovered,
                "to": Web3.to_hex(decoded["to"]) if decoded["to"] else None,
                "gas": decoded["gas"],
                "maxFeePerGas": decoded["maxFeePerGas"],
                "maxPriorityFeePerGas": decoded["maxPriorityFeePerGas"],
                "value": decoded["value"],
                "nonce": decoded["nonce"],
                "data": Web3.to_hex(decoded["data"]),
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LoveEngineError("signer_response_invalid", "transaction fields") from exc
    if actual != expected:
        raise LoveEngineError("signed_transaction_mismatch", "request fields")
    return Web3.to_hex(raw)


def verify_signed_transaction(
    transaction: dict[str, Any], expected_address: str, raw_transaction: Any
) -> str:
    return _verify_signed_transaction(
        to_checksum_address(expected_address),
        canonical_transaction_request(transaction),
        raw_transaction,
    )


def _validate_relay_message(config: ExternalSignerConfig, message: str) -> None:
    lines = message.splitlines()
    if len(lines) != 6 or lines[0] != "LoveEngine Relay Authentication":
        raise LoveEngineError("message_signing_not_allowed", "relay challenge")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if "=" not in line:
            raise LoveEngineError("message_signing_not_allowed", "relay challenge")
        key, value = line.split("=", 1)
        fields[key] = value
    allowed_registries = {
        registry for _, _, registry, _ in config.allowed_typed_data
    }
    try:
        valid = (
            fields.get("schema") == "loveengine.relay-challenge/1"
            and fields.get("chain_id") == config.chain_id
            and to_checksum_address(fields.get("registry")) in allowed_registries
            and to_checksum_address(fields.get("node")) == config.address
            and re.fullmatch(r"[0-9a-f]{64}", fields.get("nonce", ""))
            is not None
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise LoveEngineError("message_signing_not_allowed", "relay challenge")


def _verify_message_signature(address: str, message: str, signature: Any) -> str:
    normalized = _validate_signature(signature)
    try:
        recovered = Account.recover_message(
            encode_defunct(text=message), signature=normalized
        )
    except Exception as exc:
        raise LoveEngineError("signer_response_invalid", "message recovery") from exc
    if to_checksum_address(recovered) != address:
        raise LoveEngineError("signer_address_mismatch", recovered)
    return normalized


def _verify_typed_signature(
    address: str, typed_data: dict[str, Any], signature: Any
) -> str:
    normalized = _validate_signature(signature)
    try:
        recovered = Account.recover_message(
            encode_typed_data(full_message=typed_data), signature=normalized
        )
    except Exception as exc:
        raise LoveEngineError("signer_response_invalid", "typed data recovery") from exc
    if to_checksum_address(recovered) != address:
        raise LoveEngineError("signer_address_mismatch", recovered)
    return normalized


class AnvilRpcSigner:
    def __init__(
        self, config: ExternalSignerConfig, *, web3: Any | None = None
    ) -> None:
        if config.kind != "anvil_rpc":
            raise LoveEngineError("wrong_signer_kind", config.kind)
        self.config = config
        self.address = config.address
        self.chain_id = config.chain_id
        self.role = config.role
        self.web3 = web3 or Web3(
            HTTPProvider(config.endpoint, request_kwargs={"timeout": 3})
        )
        if not self.web3.is_connected():
            raise LoveEngineError("signer_unavailable", config.endpoint, 4)
        if str(self.web3.eth.chain_id) != "31337":
            raise LoveEngineError("wrong_chain_id", str(self.web3.eth.chain_id), 4)

    def _request(self, method: str, params: list[Any]) -> Any:
        response = self.web3.provider.make_request(method, params)
        if "error" in response or "result" not in response:
            raise LoveEngineError("signer_error", str(response.get("error")), 4)
        return response["result"]

    def sign_message(self, message: str) -> str:
        if self.role != "observation_node":
            raise LoveEngineError("signer_operation_not_allowed", self.role)
        _validate_relay_message(self.config, message)
        signature = self._request(
            "eth_sign", [self.address, Web3.to_hex(text=message)]
        )
        return _verify_message_signature(self.address, message, signature)

    def sign_typed_data(self, typed_data: dict[str, Any]) -> str:
        _validate_typed_scope(self.config, typed_data)
        signature = self._request(
            "eth_signTypedData_v4",
            [self.address, json.dumps(typed_data, separators=(",", ":"))],
        )
        return _verify_typed_signature(self.address, typed_data, signature)

    def sign_transaction(
        self, transaction: dict[str, Any], method_signature: str | None = None
    ) -> str:
        raise LoveEngineError("signer_operation_not_implemented", "anvil transaction")


class _ClefTransport:
    def __init__(self, config: ExternalSignerConfig) -> None:
        self.config = config
        self._request_id = 0

    def __call__(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self.config.transport == "http":
            request = Request(
                self.config.endpoint,
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(
                    request, timeout=self.config.request_timeout_seconds
                ) as response:
                    value = json.loads(response.read().decode("utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise LoveEngineError("signer_unavailable", exc.__class__.__name__, 4) from exc
        else:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                    channel.settimeout(self.config.request_timeout_seconds)
                    channel.connect(self.config.endpoint)
                    channel.sendall(encoded + b"\n")
                    chunks: list[bytes] = []
                    while True:
                        chunk = channel.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        if b"\n" in chunk:
                            break
                value = json.loads(b"".join(chunks).decode("utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise LoveEngineError("signer_unavailable", exc.__class__.__name__, 4) from exc
        if not isinstance(value, dict) or value.get("id") != self._request_id:
            raise LoveEngineError("signer_response_invalid", "JSON-RPC identity")
        if "error" in value or "result" not in value:
            raise LoveEngineError("signer_error", str(value.get("error")), 4)
        return value["result"]


class _ClefSigner:
    def __init__(
        self,
        config: ExternalSignerConfig,
        *,
        request: Callable[[str, list[Any]], Any] | None = None,
    ) -> None:
        if config.kind != "clef":
            raise LoveEngineError("wrong_signer_kind", config.kind)
        self.config = config
        self.address = config.address
        self.chain_id = config.chain_id
        self.role = config.role
        self._request = request or _ClefTransport(config)

    def probe(self) -> dict[str, Any]:
        version = self._request("account_version", [])
        if version != CLEF_EXTERNAL_API_VERSION:
            raise LoveEngineError("clef_api_version_mismatch", str(version), 4)
        return {
            "connected": True,
            "external_api_version": version,
            "approval_mode": self.config.approval_mode,
        }

    def sign_message(self, message: str) -> str:
        if self.role != "observation_node":
            raise LoveEngineError("signer_operation_not_allowed", self.role)
        _validate_relay_message(self.config, message)
        signature = self._request(
            "account_signData",
            ["text/plain", self.address, Web3.to_hex(text=message)],
        )
        return _verify_message_signature(self.address, message, signature)

    def sign_typed_data(self, typed_data: dict[str, Any]) -> str:
        _validate_typed_scope(self.config, typed_data)
        signature = self._request(
            "account_signTypedData", [self.address, typed_data]
        )
        return _verify_typed_signature(self.address, typed_data, signature)

    def sign_transaction(
        self, transaction: dict[str, Any], method_signature: str | None = None
    ) -> str:
        if self.role != "publisher":
            raise LoveEngineError("signer_operation_not_allowed", self.role)
        expected = _validate_transaction_scope(self.config, transaction)
        params: list[Any] = [transaction]
        if method_signature is not None:
            params.append(method_signature)
        result = self._request("account_signTransaction", params)
        if not isinstance(result, dict) or not isinstance(result.get("raw"), str):
            raise LoveEngineError("signer_response_invalid", "missing raw transaction")
        return _verify_signed_transaction(self.address, expected, result["raw"])


def build_signer_client(
    config: ExternalSignerConfig,
    *,
    web3: Any | None = None,
) -> SignerClient:
    if config.kind == "anvil_rpc":
        return AnvilRpcSigner(config, web3=web3)
    raise LoveEngineError(
        "clef_verified_factory_required",
        "Clef clients are available only through context-validating operator commands",
    )
