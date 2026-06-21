"""Machine-readable LoveEngine command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .errors import LoveEngineError
from .evidence import build_evidence_bundle
from .demo import run_local_loop
from .fixtures import generate_witness_fixtures
from .jsonio import read_json, write_json
from .manifest import DEFAULT_MANIFEST, verify_manifest
from .network_demo import run_network_demo
from .network_node import connect_node
from .network_protocol import (
    build_bootstrap,
    verify_bootstrap,
    verify_node_profile,
)
from .network_transcript import verify_network_transcript
from .network_typed_data import build_node_profile_typed_data
from .node_profile import build_node_profile
from .registry import publish_plan, verify_release
from .relayer import plan_batch
from .relay import RelayStore
from .relay_server import serve_forever
from .transcript import verify_transcript
from .typed_data import build_register_typed_data, build_vote_typed_data


class MachineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LoveEngineError("invalid_arguments", message)


def build_parser() -> argparse.ArgumentParser:
    parser = MachineArgumentParser(prog="loveengine")
    commands = parser.add_subparsers(dest="command")

    manifest = commands.add_parser("manifest")
    manifest_commands = manifest.add_subparsers(dest="manifest_command")
    manifest_verify = manifest_commands.add_parser("verify")
    manifest_verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)

    node = commands.add_parser("node")
    node_commands = node.add_subparsers(dest="node_command")
    node_declare = node_commands.add_parser("declare")
    node_declare.add_argument("--config", type=Path, required=True)
    node_declare.add_argument("--output", type=Path)
    node_profile = node_commands.add_parser("profile")
    node_profile_commands = node_profile.add_subparsers(dest="node_profile_command")
    node_profile_sign = node_profile_commands.add_parser("sign")
    node_profile_sign.add_argument("--input", type=Path, required=True)

    fixture = commands.add_parser("fixture")
    fixture_commands = fixture.add_subparsers(dest="fixture_command")
    fixture_generate = fixture_commands.add_parser("generate")
    fixture_generate.add_argument("--witnesses", type=int, required=True)
    fixture_generate.add_argument("--output", type=Path, required=True)

    evidence = commands.add_parser("evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command")
    evidence_build = evidence_commands.add_parser("build")
    evidence_build.add_argument("--session", type=Path, required=True)
    evidence_build.add_argument("--output", type=Path, required=True)

    transcript = commands.add_parser("transcript")
    transcript_commands = transcript.add_subparsers(dest="transcript_command")
    transcript_verify = transcript_commands.add_parser("verify")
    transcript_verify.add_argument("path", type=Path)

    eip712 = commands.add_parser("eip712")
    eip712_commands = eip712.add_subparsers(dest="eip712_command")
    register_message = eip712_commands.add_parser("register-message")
    register_message.add_argument("--input", type=Path, required=True)
    vote_message = eip712_commands.add_parser("vote-message")
    vote_message.add_argument("--proposal-id", type=int, required=True)
    vote_message.add_argument("--input", type=Path, required=True)

    relayer = commands.add_parser("relayer")
    relayer_commands = relayer.add_subparsers(dest="relayer_command")
    for name in ("batch-register", "batch-vote"):
        batch = relayer_commands.add_parser(name)
        batch.add_argument("--input", type=Path, required=True)
        batch.add_argument("--dry-run", action="store_true")

    demo = commands.add_parser("demo")
    demo_commands = demo.add_subparsers(dest="demo_command")
    local_loop = demo_commands.add_parser("local-loop")
    local_loop.add_argument("--config", type=Path)
    local_loop.add_argument("--output", type=Path, required=True)

    registry = commands.add_parser("registry")
    registry_commands = registry.add_subparsers(dest="registry_command")
    registry_publish = registry_commands.add_parser("publish")
    registry_publish.add_argument("--input", type=Path, required=True)
    registry_publish.add_argument("--dry-run", action="store_true")
    registry_verify = registry_commands.add_parser("verify")
    registry_verify.add_argument("--release", type=Path, required=True)
    registry_verify.add_argument("--artifact", type=Path, required=True)
    registry_verify.add_argument("--chain-id", required=True)
    registry_verify.add_argument("--registry", required=True)
    registry_verify.add_argument("--publisher", required=True)

    bootstrap = commands.add_parser("bootstrap")
    bootstrap_commands = bootstrap.add_subparsers(dest="bootstrap_command")
    bootstrap_build = bootstrap_commands.add_parser("build")
    bootstrap_build.add_argument("--input", type=Path, required=True)
    bootstrap_build.add_argument("--output", type=Path)
    bootstrap_verify = bootstrap_commands.add_parser("verify")
    bootstrap_verify.add_argument("path", type=Path)
    bootstrap_verify.add_argument("--chain-id", required=True)
    bootstrap_verify.add_argument("--registry", required=True)

    relay = commands.add_parser("relay")
    relay_commands = relay.add_subparsers(dest="relay_command")
    relay_serve = relay_commands.add_parser("serve")
    relay_serve.add_argument("--bootstrap", type=Path, required=True)
    relay_serve.add_argument("--db", type=Path, required=True)
    relay_serve.add_argument("--host", default="127.0.0.1")
    relay_serve.add_argument("--port", type=int, default=8765)

    node_connect = node_commands.add_parser("connect")
    node_connect.add_argument("--url", required=True)
    node_connect.add_argument("--profile", type=Path, required=True)
    node_connect.add_argument("--rpc-url")
    node_connect.add_argument("--address")
    node_connect.add_argument("--expected-tasks", type=int, default=0)
    node_connect.add_argument("--output", type=Path)
    node_connect.add_argument("--dry-run", action="store_true")

    network = commands.add_parser("network")
    network_commands = network.add_subparsers(dest="network_command")
    network_demo = network_commands.add_parser("demo")
    network_demo.add_argument("--nodes", type=int, default=3)
    network_demo.add_argument("--output", type=Path, required=True)
    network_transcript = network_commands.add_parser("transcript")
    network_transcript_commands = network_transcript.add_subparsers(
        dest="network_transcript_command"
    )
    network_transcript_verify = network_transcript_commands.add_parser("verify")
    network_transcript_verify.add_argument("path", type=Path)
    return parser


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "manifest" and args.manifest_command == "verify":
        return verify_manifest(args.manifest)
    if args.command == "node" and args.node_command == "declare":
        profile = build_node_profile(read_json(args.config))
        if args.output:
            write_json(args.output, profile)
        return profile
    if (
        args.command == "node"
        and args.node_command == "profile"
        and args.node_profile_command == "sign"
    ):
        value = read_json(args.input)
        return {
            "typed_data": build_node_profile_typed_data(
                value["chain_id"],
                value["registry"],
                value["profile"],
            ),
            "signer_required": True,
        }
    if args.command == "node" and args.node_command == "connect":
        signed_profile = read_json(args.profile)
        node_address = verify_node_profile(signed_profile)
        if not args.dry_run:
            if not args.rpc_url or not args.address:
                raise LoveEngineError(
                    "local_signer_required",
                    "live connect requires --rpc-url and --address",
                    4,
                )
            return connect_node(
                url=args.url,
                rpc_url=args.rpc_url,
                address=args.address,
                profile_path=args.profile,
                expected_tasks=args.expected_tasks,
                output=args.output,
            )
        return {
            "connected": False,
            "dry_run": True,
            "node": node_address,
            "url": args.url,
        }
    if args.command == "fixture" and args.fixture_command == "generate":
        paths = generate_witness_fixtures(args.witnesses, args.output)
        return {"generated": len(paths), "output": str(args.output)}
    if args.command == "evidence" and args.evidence_command == "build":
        evidence = build_evidence_bundle(read_json(args.session))
        write_json(args.output, evidence)
        return evidence
    if args.command == "transcript" and args.transcript_command == "verify":
        return verify_transcript(read_json(args.path))
    if args.command == "eip712" and args.eip712_command == "register-message":
        return build_register_typed_data(read_json(args.input))
    if args.command == "eip712" and args.eip712_command == "vote-message":
        value = read_json(args.input)
        if int(value["proposal_id"]) != args.proposal_id:
            raise LoveEngineError(
                "proposal_id_mismatch",
                "CLI proposal ID does not match input",
            )
        return build_vote_typed_data(value)
    if args.command == "relayer" and args.relayer_command:
        return plan_batch(
            args.relayer_command,
            read_json(args.input),
            args.dry_run,
        )
    if args.command == "demo" and args.demo_command == "local-loop":
        if args.config:
            read_json(args.config)
        return run_local_loop(args.output)
    if args.command == "registry" and args.registry_command == "publish":
        if not args.dry_run:
            raise LoveEngineError(
                "local_signer_required",
                "registry publish requires a local signer adapter",
                4,
            )
        return publish_plan(read_json(args.input))
    if args.command == "registry" and args.registry_command == "verify":
        return verify_release(
            read_json(args.release),
            args.artifact,
            expected_chain_id=args.chain_id,
            expected_registry=args.registry,
            expected_publisher=args.publisher,
        )
    if args.command == "bootstrap" and args.bootstrap_command == "build":
        value = read_json(args.input)
        bootstrap = build_bootstrap(
            value["publisher"],
            value["nodes"],
            value["sequence"],
            value["valid_until"],
        )
        bootstrap["signature"] = value.get("signature", "0x")
        if args.output:
            write_json(args.output, bootstrap)
        return bootstrap
    if args.command == "bootstrap" and args.bootstrap_command == "verify":
        value = read_json(args.path)
        verify_bootstrap(value, args.chain_id, args.registry)
        return {
            "valid": True,
            "publisher": value["publisher"],
            "node_count": len(value["directory"]),
        }
    if args.command == "relay" and args.relay_command == "serve":
        bootstrap = read_json(args.bootstrap)
        asyncio.run(
            serve_forever(
                RelayStore(args.db),
                bootstrap,
                args.host,
                args.port,
            )
        )
        return {"stopped": True}
    if args.command == "network" and args.network_command == "demo":
        return run_network_demo(args.output, args.nodes)
    if (
        args.command == "network"
        and args.network_command == "transcript"
        and args.network_transcript_command == "verify"
    ):
        return verify_network_transcript(read_json(args.path))
    raise LoveEngineError("missing_command", "a command and subcommand are required")


def main() -> int:
    parser = build_parser()
    try:
        args = parser.parse_args()
        emit(dispatch(args))
        return 0
    except LoveEngineError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return exc.exit_code
    except (KeyError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {"error": {"code": "invalid_input", "message": str(exc)}},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
