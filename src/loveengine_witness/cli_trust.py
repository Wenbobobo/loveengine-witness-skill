"""CLI parser and handlers for package, Registry, and trusted node entrypoints."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import time
from typing import Any

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .m4_network import verify_node_profile_v2
from .network_node import connect_node
from .network_protocol import verify_node_profile
from .package import build_package, install_package, package_self_check, verify_package
from .pilot_config import validate_pilot_invite_v2
from .registry import publish_plan, verify_onchain_release, verify_release
from .registry_transactions import (
    build_registry_deploy_plan,
    build_registry_publish_plan,
    sign_transaction_plan,
    submit_signed_transaction_plan,
)
from .release_identity import SKILL_VERSION
from .schema import validate_schema
from .rpc_endpoints import resolve_rpc_endpoint, validate_rpc_pair
from .signer_client import (
    _build_verified_signer_client,
    inspect_clef_binary,
    load_external_signer_config,
    verify_clef_evidence_files,
)
from .trust_policy import (
    load_node_trust_policy,
    verify_invite_against_policy,
    verify_profile_against_policy,
    verify_release_against_policy,
)


ROOT = Path(__file__).resolve().parents[2]


def add_clef_runtime_evidence_arguments(parser: Any) -> None:
    parser.add_argument("--ruleset-file", type=Path)
    parser.add_argument("--rules-attestation-file", type=Path)
    parser.add_argument("--clef-binary", type=Path)
    parser.add_argument("--expected-binary-sha256")


def _build_cli_signer(config: Any, args: argparse.Namespace) -> tuple[Any, Any]:
    return _build_verified_signer_client(
        config,
        ruleset_path=getattr(args, "ruleset_file", None),
        rules_attestation_path=getattr(args, "rules_attestation_file", None),
        binary=getattr(args, "clef_binary", None),
        expected_binary_sha256=getattr(args, "expected_binary_sha256", None),
    )


def add_node_connect_parser(node_commands: Any) -> None:
    parser = node_commands.add_parser("connect")
    parser.add_argument("--url")
    parser.add_argument("--invite", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    primary_rpc = parser.add_mutually_exclusive_group()
    primary_rpc.add_argument("--rpc-url")
    primary_rpc.add_argument("--rpc-url-file", type=Path)
    secondary_rpc = parser.add_mutually_exclusive_group()
    secondary_rpc.add_argument("--secondary-rpc-url")
    secondary_rpc.add_argument("--secondary-rpc-url-file", type=Path)
    parser.add_argument("--address")
    parser.add_argument("--signer-config", type=Path)
    add_clef_runtime_evidence_arguments(parser)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--cursor-db", type=Path)
    parser.add_argument("--verdicts", type=Path)
    parser.add_argument("--expected-tasks", type=int, default=0)
    parser.add_argument("--reconnect-attempts", type=int, default=3)
    parser.add_argument("--idle-timeout-seconds", type=float, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")


def add_registry_parser(commands: Any) -> None:
    registry = commands.add_parser("registry")
    registry_commands = registry.add_subparsers(dest="registry_command")
    publish = registry_commands.add_parser("publish")
    publish.add_argument("--input", type=Path, required=True)
    publish.add_argument("--dry-run", action="store_true")
    verify = registry_commands.add_parser("verify")
    source = verify.add_mutually_exclusive_group(required=True)
    source.add_argument("--release", type=Path)
    source.add_argument("--rpc-url")
    source.add_argument("--rpc-url-file", type=Path)
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--chain-id", required=True)
    verify.add_argument("--registry", required=True)
    verify.add_argument("--publisher", required=True)
    verify.add_argument("--skill-id", default="loveengine-witness")
    verify.add_argument("--version", default=SKILL_VERSION)
    transaction = registry_commands.add_parser("transaction")
    transaction_commands = transaction.add_subparsers(
        dest="registry_transaction_command"
    )
    for name in ("deploy-plan", "publish-plan"):
        plan = transaction_commands.add_parser(name)
        plan.add_argument("--sender", required=True)
        plan.add_argument("--nonce", type=int, required=True)
        plan.add_argument("--gas", type=int, required=True)
        plan.add_argument("--max-fee-per-gas", type=int, required=True)
        plan.add_argument("--max-priority-fee-per-gas", type=int, required=True)
        plan.add_argument("--created-at", type=int, required=True)
        plan.add_argument("--expires-at", type=int, required=True)
        plan.add_argument("--output", type=Path, required=True)
    transaction_commands.choices["deploy-plan"].add_argument(
        "--artifact", type=Path, required=True
    )
    transaction_commands.choices["publish-plan"].add_argument(
        "--release", type=Path, required=True
    )
    sign = transaction_commands.add_parser("sign")
    sign.add_argument("--plan", type=Path, required=True)
    sign.add_argument("--signer-config", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)
    add_clef_runtime_evidence_arguments(sign)
    submit = transaction_commands.add_parser("submit")
    submit.add_argument("--plan", type=Path, required=True)
    submit.add_argument("--signed", type=Path, required=True)
    submit_rpc = submit.add_mutually_exclusive_group(required=True)
    submit_rpc.add_argument("--rpc-url")
    submit_rpc.add_argument("--rpc-url-file", type=Path)


def add_signer_parser(commands: Any) -> None:
    signer = commands.add_parser("signer")
    signer_commands = signer.add_subparsers(dest="signer_command")
    inspect = signer_commands.add_parser("inspect")
    inspect.add_argument("--config", type=Path, required=True)
    add_clef_runtime_evidence_arguments(inspect)
    inspect.add_argument("--probe", action="store_true")


def add_package_parser(commands: Any) -> None:
    package = commands.add_parser("package")
    package_commands = package.add_subparsers(dest="package_command")
    build = package_commands.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    verify = package_commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    verify_trust = verify.add_mutually_exclusive_group(required=True)
    verify_trust.add_argument("--expected-package-hash")
    verify_trust.add_argument("--integrity-only", action="store_true")
    install = package_commands.add_parser("install")
    install.add_argument("archive", type=Path)
    install.add_argument("--target", type=Path, required=True)
    install_trust = install.add_mutually_exclusive_group(required=True)
    install_trust.add_argument("--expected-package-hash")
    install_trust.add_argument("--integrity-only", action="store_true")
    check = package_commands.add_parser("self-check")
    check.add_argument("--root", type=Path, required=True)
    check_trust = check.add_mutually_exclusive_group(required=True)
    check_trust.add_argument("--expected-package-hash")
    check_trust.add_argument("--integrity-only", action="store_true")


def handle_node_connect(args: argparse.Namespace) -> dict[str, Any]:
    signed_profile = read_json(args.profile)
    invite = read_json(args.invite) if args.invite else None
    trust_policy = (
        load_node_trust_policy(args.trust_policy) if args.trust_policy else None
    )
    signer_config = (
        load_external_signer_config(args.signer_config)
        if args.signer_config
        else None
    )
    if invite is not None:
        if invite.get("schema_version") == "loveengine.pilot-invite/1":
            validate_schema(invite, "pilot-invite-v1.schema.json")
        elif invite.get("schema_version") == "loveengine.pilot-invite/2":
            validate_pilot_invite_v2(invite, current_time=int(time()))
        else:
            raise LoveEngineError(
                "unsupported_schema_version", str(invite.get("schema_version"))
            )
    url = args.url or (invite["relay_url"] if invite else None)
    if not url:
        raise LoveEngineError(
            "invalid_arguments", "node connect requires --url or --invite", 2
        )
    if trust_policy is not None:
        verify_profile_against_policy(signed_profile, trust_policy)
        if invite is not None:
            verify_invite_against_policy(invite, trust_policy)
    elif invite is not None:
        if signed_profile["chain_id"] != invite["chain_id"]:
            raise LoveEngineError("wrong_chain_id", "profile chainId does not match invite")
        if signed_profile["registry"].lower() != invite["registry"].lower():
            raise LoveEngineError("wrong_registry", "profile Registry does not match invite")
    if signed_profile.get("schema_version") == "loveengine.signed-agent-node-profile/2":
        node_address = verify_node_profile_v2(signed_profile)
    else:
        node_address = verify_node_profile(signed_profile)
    if signer_config is not None:
        if signer_config.role != "observation_node":
            raise LoveEngineError("wrong_signer_role", signer_config.role)
        if to_checksum_address(signer_config.address) != to_checksum_address(
            node_address
        ):
            raise LoveEngineError("wrong_signer_address", signer_config.address)
        if trust_policy is not None and signer_config.chain_id != trust_policy["chain_id"]:
            raise LoveEngineError("wrong_chain_id", signer_config.chain_id)
    if args.expected_tasks < 0:
        raise LoveEngineError("invalid_arguments", "expected tasks cannot be negative", 2)
    if args.reconnect_attempts < 0 or args.reconnect_attempts > 10:
        raise LoveEngineError(
            "invalid_arguments",
            "reconnect attempts must be between 0 and 10",
            2,
        )
    if args.idle_timeout_seconds < 1 or args.idle_timeout_seconds > 900:
        raise LoveEngineError(
            "invalid_arguments",
            "idle timeout must be between 1 and 900 seconds",
            2,
        )
    if args.dry_run:
        return {
            "connected": False,
            "dry_run": True,
            "node": node_address,
            "url": url,
            "trust_bound": False,
            "verification_level": "connection_plan",
            "reconnect_attempts": args.reconnect_attempts,
            "idle_timeout_seconds": args.idle_timeout_seconds,
            "signer": (
                {
                    "kind": signer_config.kind,
                    "role": signer_config.role,
                    "address": signer_config.address,
                    "chain_id": signer_config.chain_id,
                    "approval_mode": signer_config.approval_mode,
                    "ruleset_sha256": signer_config.ruleset_sha256,
                    "rules_attestation_sha256": signer_config.rules_attestation_sha256,
                }
                if signer_config is not None
                else None
            ),
            "trust_policy": {
                "path": str(args.trust_policy.resolve()),
                "chain_id": trust_policy["chain_id"],
                "registry": trust_policy["registry"],
                "publisher": trust_policy["publisher"],
                "skill_id": trust_policy["skill_id"],
                "version": trust_policy["version"],
                "package_hash": trust_policy["package_hash"],
                "manifest_hash": trust_policy["manifest_hash"],
                "allowed_issuers": trust_policy["allowed_issuers"],
            }
            if trust_policy
            else None,
            "invite": {
                "dashboard_url": invite["dashboard_url"],
                "server_url": invite.get("server_url", invite.get("participant_url")),
                "package_hash": invite["package_hash"],
            }
            if invite
            else None,
        }
    if trust_policy is None:
        raise LoveEngineError(
            "trust_policy_required",
            "live connect requires --trust-policy from a trusted out-of-band source",
            4,
        )
    signer_address = signer_config.address if signer_config is not None else args.address
    rpc_url = resolve_rpc_endpoint(
        direct_url=args.rpc_url,
        url_file=args.rpc_url_file,
        chain_id=trust_policy["chain_id"],
        label="primary",
        required=True,
    )
    secondary_rpc_url = resolve_rpc_endpoint(
        direct_url=args.secondary_rpc_url,
        url_file=args.secondary_rpc_url_file,
        chain_id=trust_policy["chain_id"],
        label="secondary",
    )
    if not signer_address or not args.package:
        raise LoveEngineError(
            "trusted_release_required",
            "live connect requires --package, an RPC endpoint, and a signer address",
            4,
        )
    if (
        signed_profile.get("schema_version")
        == "loveengine.signed-agent-node-profile/2"
        and invite is None
    ):
        raise LoveEngineError(
            "pilot_invite_required",
            "V2 live connect requires an invite to bind evidence HTTP origin",
            4,
        )
    if to_checksum_address(signer_address) != to_checksum_address(node_address):
        raise LoveEngineError("wrong_node_address", "signer address does not match profile")
    if signer_config is not None:
        if args.address and to_checksum_address(args.address) != signer_config.address:
            raise LoveEngineError("wrong_signer_address", args.address)
    if trust_policy["chain_id"] == "11155111":
        if signer_config is None or signer_config.kind != "clef":
            raise LoveEngineError(
                "external_signer_required",
                "Sepolia node connect requires a Clef signer config",
            )
        validate_rpc_pair(
            rpc_url,
            secondary_rpc_url,
            chain_id=trust_policy["chain_id"],
            require_secondary=True,
        )
    release = verify_onchain_release(
        args.package,
        rpc_url=rpc_url,
        expected_chain_id=trust_policy["chain_id"],
        registry=trust_policy["registry"],
        publisher=trust_policy["publisher"],
        skill_id=trust_policy["skill_id"],
        version=trust_policy["version"],
    )
    verify_release_against_policy(release, trust_policy)
    if secondary_rpc_url:
        secondary_release = verify_onchain_release(
            args.package,
            rpc_url=secondary_rpc_url,
            expected_chain_id=trust_policy["chain_id"],
            registry=trust_policy["registry"],
            publisher=trust_policy["publisher"],
            skill_id=trust_policy["skill_id"],
            version=trust_policy["version"],
        )
        verify_release_against_policy(secondary_release, trust_policy)
        for field in (
            "chain_id",
            "registry",
            "publisher",
            "skill_id",
            "version",
            "package_hash",
            "manifest_hash",
            "status",
            "published_at",
        ):
            if str(secondary_release[field]).lower() != str(release[field]).lower():
                raise LoveEngineError("rpc_release_disagreement", field)
    release_check_count = 0

    def revalidate_release() -> None:
        nonlocal release_check_count
        release_check_count += 1
        if release_check_count == 1:
            return
        current = verify_onchain_release(
            args.package,
            rpc_url=rpc_url,
            expected_chain_id=trust_policy["chain_id"],
            registry=trust_policy["registry"],
            publisher=trust_policy["publisher"],
            skill_id=trust_policy["skill_id"],
            version=trust_policy["version"],
        )
        verify_release_against_policy(current, trust_policy)
        if secondary_rpc_url:
            secondary = verify_onchain_release(
                args.package,
                rpc_url=secondary_rpc_url,
                expected_chain_id=trust_policy["chain_id"],
                registry=trust_policy["registry"],
                publisher=trust_policy["publisher"],
                skill_id=trust_policy["skill_id"],
                version=trust_policy["version"],
            )
            verify_release_against_policy(secondary, trust_policy)
            if (
                secondary["package_hash"].lower() != current["package_hash"].lower()
                or secondary["manifest_hash"].lower()
                != current["manifest_hash"].lower()
                or secondary["status"] != current["status"]
            ):
                raise LoveEngineError("rpc_release_disagreement", "release")

    cursor_database = args.cursor_db
    if cursor_database is None:
        cursor_database = args.profile.with_suffix(".cursor.sqlite")
    return connect_node(
        url=url,
        rpc_url=rpc_url,
        address=signer_address,
        profile_path=args.profile,
        expected_tasks=args.expected_tasks,
        expected_issuer=None,
        allowed_issuers=trust_policy["allowed_issuers"],
        expected_manifest_hash=release["manifest_hash"],
        cursor_database=cursor_database,
        verdicts_path=args.verdicts,
        output=args.output,
        allowed_http_origin=(
            invite.get("server_url", invite.get("participant_url"))
            if invite is not None
            else None
        ),
        reconnect_attempts=args.reconnect_attempts,
        idle_timeout_seconds=args.idle_timeout_seconds,
        before_connect=revalidate_release,
        signer_client=(_build_cli_signer(signer_config, args)[0] if signer_config else None),
    )


def handle_registry(args: argparse.Namespace) -> dict[str, Any]:
    if args.registry_command == "publish":
        if not args.dry_run:
            raise LoveEngineError(
                "local_signer_required",
                "registry publish requires a local signer adapter",
                4,
            )
        return publish_plan(read_json(args.input))
    if args.registry_command == "verify":
        rpc_url = resolve_rpc_endpoint(
            direct_url=args.rpc_url,
            url_file=args.rpc_url_file,
            chain_id=args.chain_id,
            label="registry_verify",
        )
        if rpc_url:
            return verify_onchain_release(
                args.artifact,
                rpc_url=rpc_url,
                expected_chain_id=args.chain_id,
                registry=args.registry,
                publisher=args.publisher,
                skill_id=args.skill_id,
                version=args.version,
            )
        return verify_release(
            read_json(args.release),
            args.artifact,
            expected_chain_id=args.chain_id,
            expected_registry=args.registry,
            expected_publisher=args.publisher,
        )
    if args.registry_command == "transaction":
        command = args.registry_transaction_command
        if command in {"deploy-plan", "publish-plan"}:
            common = {
                "sender": args.sender,
                "nonce": args.nonce,
                "gas": args.gas,
                "max_fee_per_gas": args.max_fee_per_gas,
                "max_priority_fee_per_gas": args.max_priority_fee_per_gas,
                "created_at": args.created_at,
                "expires_at": args.expires_at,
            }
            plan = (
                build_registry_deploy_plan(args.artifact, **common)
                if command == "deploy-plan"
                else build_registry_publish_plan(read_json(args.release), **common)
            )
            write_json(args.output, plan)
            return {
                "created": True,
                "operation": plan["operation"],
                "plan_hash": plan["plan_hash"],
                "request_hash": plan["request_hash"],
                "output": str(args.output.resolve()),
            }
        if command == "sign":
            plan = read_json(args.plan)
            config = load_external_signer_config(args.signer_config)
            signer, _ = _build_cli_signer(config, args)
            signed = sign_transaction_plan(plan, signer)
            write_json(args.output, signed)
            return {
                "signed": True,
                "plan_hash": signed["plan_hash"],
                "request_hash": signed["request_hash"],
                "transaction_hash": signed["transaction_hash"],
                "signer": signed["signer"],
                "output": str(args.output.resolve()),
            }
        if command == "submit":
            rpc_url = resolve_rpc_endpoint(
                direct_url=args.rpc_url,
                url_file=args.rpc_url_file,
                chain_id="11155111",
                label="transaction_submit",
                required=True,
            )
            return submit_signed_transaction_plan(
                read_json(args.plan),
                read_json(args.signed),
                rpc_url=rpc_url,
            )
        raise LoveEngineError(
            "missing_command", "registry transaction subcommand is required"
        )
    raise LoveEngineError("missing_command", "registry subcommand is required")


def handle_signer(args: argparse.Namespace) -> dict[str, Any]:
    config = load_external_signer_config(args.config)
    summary = {
        "kind": config.kind,
        "role": config.role,
        "address": config.address,
        "chain_id": config.chain_id,
        "approval_mode": config.approval_mode,
        "ruleset_sha256": config.ruleset_sha256,
        "rules_attestation_sha256": config.rules_attestation_sha256,
        "allowed_typed_data_count": len(config.allowed_typed_data),
        "approved_transaction_request_count": len(
            config.approved_transaction_request_hashes
        ),
    }
    if args.signer_command == "inspect":
        evidence_requested = bool(
            args.ruleset_file or args.rules_attestation_file
        )
        if evidence_requested and not (
            args.ruleset_file and args.rules_attestation_file
        ):
            raise LoveEngineError(
                "clef_evidence_files_required",
                "ruleset and rules attestation files must be provided together",
            )
        evidence = (
            verify_clef_evidence_files(
                config,
                ruleset_path=args.ruleset_file,
                rules_attestation_path=args.rules_attestation_file,
            )
            if evidence_requested
            else None
        )
        binary_requested = bool(
            args.clef_binary or args.expected_binary_sha256
        )
        if binary_requested and not (
            args.clef_binary and args.expected_binary_sha256
        ):
            raise LoveEngineError(
                "clef_binary_evidence_required",
                "binary path and expected SHA-256 must be provided together",
            )
        binary = (
            inspect_clef_binary(
                args.clef_binary,
                expected_sha256=args.expected_binary_sha256,
            )
            if binary_requested
            else None
        )
        probe = None
        if args.probe:
            if config.kind != "clef" or binary is None or evidence is None:
                raise LoveEngineError(
                    "clef_probe_evidence_required",
                    "live Clef probe requires binary and rules evidence",
                )
            client, _ = _build_cli_signer(config, args)
            probe = client.probe()
        return {
            "config_valid": True,
            "evidence_verified": evidence is not None,
            "binary_verified": binary is not None,
            "live_probe_verified": probe is not None,
            **summary,
            "evidence": evidence,
            "binary": binary,
            "probe": probe,
        }
    raise LoveEngineError("missing_command", "signer subcommand is required")


def handle_package(args: argparse.Namespace) -> dict[str, Any]:
    if args.package_command == "build":
        result = build_package(ROOT, args.output)
        return {
            "archive": str(result.archive.resolve()),
            "archive_sha256": result.sha256,
            "archive_keccak256": result.keccak256,
            "file_count": result.file_count,
            "checksums": str(result.checksums.resolve()),
            "sbom": str(result.sbom.resolve()),
        }
    if args.package_command == "verify":
        return verify_package(
            args.archive,
            expected_package_hash=args.expected_package_hash,
            integrity_only=args.integrity_only,
        )
    if args.package_command == "install":
        return install_package(
            args.archive,
            args.target,
            expected_package_hash=args.expected_package_hash,
            integrity_only=args.integrity_only,
        )
    if args.package_command == "self-check":
        return package_self_check(
            args.root,
            expected_package_hash=args.expected_package_hash,
            integrity_only=args.integrity_only,
        )
    raise LoveEngineError("missing_command", "package subcommand is required")
