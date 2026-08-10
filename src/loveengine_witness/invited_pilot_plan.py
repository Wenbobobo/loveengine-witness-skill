"""Secret-free planning contract for an invited Sepolia pilot."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from yaml.constructor import ConstructorError

from .errors import LoveEngineError
from .rpc_endpoints import resolve_rpc_endpoint, validate_rpc_pair
from .schema import validate_schema
from .secrets import read_restricted_text_file


SCHEMA_VERSION = "loveengine.invited-public-pilot-plan/1"
MAX_PLAN_BYTES = 1024 * 1024
ZERO_ADDRESS = "0x" + "0" * 40
ZERO_BYTES32 = "0x" + "0" * 64
ZERO_SHA256 = "sha256:" + "0" * 64
ZERO_COMMIT = "0" * 40
INPUT_FILE_FIELDS = {
    "package_archive",
    "release_file",
    "trust_policy_file",
    "primary_url_file",
    "secondary_url_file",
    "config_file",
    "ruleset_file",
    "rules_attestation_file",
    "clef_binary",
    "profile_file",
    "write_token_file",
    "invite_file",
    "service_config_file",
}
OUTPUT_PATH_FIELDS = {"cursor_database"}
PATH_FIELDS = INPUT_FILE_FIELDS | OUTPUT_PATH_FIELDS
INLINE_SECRET_KEYS = {
    "private_key",
    "private-key",
    "mnemonic",
    "password",
    "auth_key",
    "tailscale_auth_key",
    "rpc_url",
    "write_token",
}


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _walk(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    records = [(path, value)]
    if isinstance(value, dict):
        for key, child in value.items():
            records.extend(_walk(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            records.extend(_walk(child, (*path, str(index))))
    return records


def _reject_inline_secrets(value: dict[str, Any]) -> None:
    for path, child in _walk(value):
        if not path:
            continue
        key = path[-1].lower()
        if key in INLINE_SECRET_KEYS:
            raise LoveEngineError(
                "pilot_plan_inline_secret_field",
                ".".join(path),
            )
        if key in PATH_FIELDS and isinstance(child, str):
            lowered = child.strip().lower()
            if lowered.startswith(("http://", "https://", "ws://", "wss://")):
                raise LoveEngineError(
                    "pilot_plan_secret_must_use_file",
                    ".".join(path),
                )


def _require_unique(values: list[str], label: str) -> None:
    normalized = [value.lower() for value in values]
    if len(set(normalized)) != len(normalized):
        raise LoveEngineError("pilot_plan_duplicate_identity", label)


def _input_path(source: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = source.parent / candidate
    return candidate.resolve()


def _validate_semantics(value: dict[str, Any], source: Path) -> None:
    rpc = value["rpc"]
    if _input_path(source, rpc["primary_url_file"]) == _input_path(
        source, rpc["secondary_url_file"]
    ):
        raise LoveEngineError(
            "pilot_plan_rpc_files_not_independent",
            "primary_url_file and secondary_url_file must differ",
        )

    participants = value["participants"]
    _require_unique([item["id"] for item in participants], "participant id")
    _require_unique([item["address"] for item in participants], "participant address")
    for field in ("profile_file", "config_file", "cursor_database"):
        _require_unique(
            [str(_input_path(source, item[field])) for item in participants],
            f"participant {field}",
        )
    role_addresses = [
        value["signers"]["publisher"]["address"],
        value["signers"]["task_issuer"]["address"],
        *(item["address"] for item in participants),
    ]
    _require_unique(role_addresses, "publisher, task issuer and participant address")
    if (
        value["signers"]["publisher"]["address"].lower()
        != value["release"]["publisher"].lower()
    ):
        raise LoveEngineError(
            "pilot_plan_publisher_mismatch",
            "release.publisher must equal signers.publisher.address",
        )
    if len({item["operator_group"] for item in participants}) < 2:
        raise LoveEngineError(
            "pilot_plan_operator_groups_insufficient",
            "at least two operator groups are required",
        )
    if len({item["network_group"] for item in participants}) < 2:
        raise LoveEngineError(
            "pilot_plan_network_groups_insufficient",
            "at least two network groups are required",
        )

    pilot = value["pilot"]
    admin = urlsplit(pilot["admin_url"])
    participant = urlsplit(pilot["participant_loopback_url"])
    if admin.port == participant.port:
        raise LoveEngineError(
            "pilot_plan_surfaces_not_separated",
            "admin and participant listeners must use different ports",
        )

    if value["execution_requested"]:
        experiment = value["experiment"]
        release = value["release"]
        signers = value["signers"]
        sentinel_values = {
            experiment["source_commit"],
            release["registry"].lower(),
            release["publisher"].lower(),
            release["package_hash"].lower(),
            release["manifest_hash"].lower(),
            signers["publisher"]["address"].lower(),
            signers["task_issuer"]["address"].lower(),
            *(item["address"].lower() for item in participants),
        }
        forbidden = {ZERO_COMMIT, ZERO_ADDRESS, ZERO_BYTES32}
        if sentinel_values & forbidden:
            raise LoveEngineError(
                "pilot_plan_placeholder_present",
                "execution requires real commit, addresses and release hashes",
            )
        expected_binary_hashes = [
            signers["publisher"]["expected_binary_sha256"],
            signers["task_issuer"]["expected_binary_sha256"],
            *(item["expected_binary_sha256"] for item in participants),
        ]
        if ZERO_SHA256 in expected_binary_hashes or any(
            int(item["address"], 16) <= 3 for item in participants
        ):
            raise LoveEngineError(
                "pilot_plan_placeholder_present",
                "replace example node addresses and Clef binary hashes",
            )
        if pilot["tailscale"]["expected_hostname"] == "pilot-host.ts.net":
            raise LoveEngineError(
                "pilot_plan_placeholder_present",
                "replace the example Tailscale hostname before execution",
            )
        if "edit-me" in {
            experiment["run_id"].lower(),
            value["evidence"]["session_id"].lower(),
        }:
            raise LoveEngineError(
                "pilot_plan_placeholder_present",
                "replace the example run and session identifiers",
            )


def _referenced_input_paths(value: dict[str, Any], source: Path) -> list[Path]:
    paths: list[Path] = []
    for path, child in _walk(value):
        if (
            not path
            or path[-1] not in INPUT_FILE_FIELDS
            or not isinstance(child, str)
        ):
            continue
        paths.append(_input_path(source, child))
    return sorted(set(paths), key=lambda item: str(item).lower())


def load_invited_pilot_plan(
    path: Path,
    *,
    check_input_files: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = path.resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise LoveEngineError("pilot_plan_unreadable", str(source)) from exc
    if len(raw) > MAX_PLAN_BYTES:
        raise LoveEngineError(
            "pilot_plan_too_large",
            f"plan exceeds {MAX_PLAN_BYTES} bytes",
        )
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LoveEngineError("pilot_plan_invalid_yaml", str(source)) from exc
    if not isinstance(value, dict):
        raise LoveEngineError("pilot_plan_invalid_root", "YAML root must be an object")

    _reject_inline_secrets(value)
    validate_schema(value, "invited-public-pilot-plan-v1.schema.json")
    _validate_semantics(value, source)
    referenced = _referenced_input_paths(value, source)
    missing: list[str] = []
    if check_input_files:
        missing = [str(item) for item in referenced if not item.is_file()]
        if missing:
            raise LoveEngineError(
                "pilot_plan_input_file_missing",
                ", ".join(missing),
            )
        rpc = value["rpc"]
        primary = resolve_rpc_endpoint(
            direct_url=None,
            url_file=_input_path(source, rpc["primary_url_file"]),
            chain_id=value["release"]["chain_id"],
            label="primary",
            required=True,
        )
        secondary = resolve_rpc_endpoint(
            direct_url=None,
            url_file=_input_path(source, rpc["secondary_url_file"]),
            chain_id=value["release"]["chain_id"],
            label="secondary",
            required=True,
        )
        validate_rpc_pair(
            primary,
            secondary,
            chain_id=value["release"]["chain_id"],
            require_secondary=True,
        )
        read_restricted_text_file(
            _input_path(source, value["pilot"]["write_token_file"]),
            label="pilot_write_token",
        )

    participants = value["participants"]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "config_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "validation_scope": "plan_structure_and_referenced_input_presence",
        "execution_authorized": False,
        "execution_requested": value["execution_requested"],
        "input_files_present": True if check_input_files else None,
        "environment": value["experiment"]["environment"],
        "source_commit": value["experiment"]["source_commit"],
        "chain_id": value["release"]["chain_id"],
        "participant_count": len(participants),
        "operator_group_count": len({item["operator_group"] for item in participants}),
        "network_group_count": len({item["network_group"] for item in participants}),
        "referenced_input_count": len(referenced),
        "input_files_checked": check_input_files,
    }
    return value, summary
