"""CLI parser and handlers for package, Registry, and trusted node entrypoints."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .jsonio import read_json
from .m4_network import verify_node_profile_v2
from .network_node import connect_node
from .network_protocol import verify_node_profile
from .package import build_package, install_package, package_self_check, verify_package
from .registry import publish_plan, verify_onchain_release, verify_release
from .release_identity import SKILL_VERSION
from .schema import validate_schema
from .trust_policy import (
    load_node_trust_policy,
    verify_invite_against_policy,
    verify_profile_against_policy,
    verify_release_against_policy,
)


ROOT = Path(__file__).resolve().parents[2]


def add_node_connect_parser(node_commands: Any) -> None:
    parser = node_commands.add_parser("connect")
    parser.add_argument("--url")
    parser.add_argument("--invite", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--rpc-url")
    parser.add_argument("--address")
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
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--chain-id", required=True)
    verify.add_argument("--registry", required=True)
    verify.add_argument("--publisher", required=True)
    verify.add_argument("--skill-id", default="loveengine-witness")
    verify.add_argument("--version", default=SKILL_VERSION)


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
    if invite is not None:
        validate_schema(invite, "pilot-invite-v1.schema.json")
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
                "server_url": invite["server_url"],
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
    if not args.rpc_url or not args.address or not args.package:
        raise LoveEngineError(
            "trusted_release_required",
            "live connect requires --package, --rpc-url, and --address",
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
    if to_checksum_address(args.address) != to_checksum_address(node_address):
        raise LoveEngineError("wrong_node_address", "signer address does not match profile")
    release = verify_onchain_release(
        args.package,
        rpc_url=args.rpc_url,
        expected_chain_id=trust_policy["chain_id"],
        registry=trust_policy["registry"],
        publisher=trust_policy["publisher"],
        skill_id=trust_policy["skill_id"],
        version=trust_policy["version"],
    )
    verify_release_against_policy(release, trust_policy)
    release_check_count = 0

    def revalidate_release() -> None:
        nonlocal release_check_count
        release_check_count += 1
        if release_check_count == 1:
            return
        current = verify_onchain_release(
            args.package,
            rpc_url=args.rpc_url,
            expected_chain_id=trust_policy["chain_id"],
            registry=trust_policy["registry"],
            publisher=trust_policy["publisher"],
            skill_id=trust_policy["skill_id"],
            version=trust_policy["version"],
        )
        verify_release_against_policy(current, trust_policy)

    cursor_database = args.cursor_db
    if cursor_database is None:
        cursor_database = args.profile.with_suffix(".cursor.sqlite")
    return connect_node(
        url=url,
        rpc_url=args.rpc_url,
        address=args.address,
        profile_path=args.profile,
        expected_tasks=args.expected_tasks,
        expected_issuer=None,
        allowed_issuers=trust_policy["allowed_issuers"],
        expected_manifest_hash=release["manifest_hash"],
        cursor_database=cursor_database,
        verdicts_path=args.verdicts,
        output=args.output,
        allowed_http_origin=(invite["server_url"] if invite is not None else None),
        reconnect_attempts=args.reconnect_attempts,
        idle_timeout_seconds=args.idle_timeout_seconds,
        before_connect=revalidate_release,
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
        if args.rpc_url:
            return verify_onchain_release(
                args.artifact,
                rpc_url=args.rpc_url,
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
    raise LoveEngineError("missing_command", "registry subcommand is required")


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
