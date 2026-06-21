"""Machine-readable LoveEngine command-line interface."""

from __future__ import annotations

import argparse
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
from .node_profile import build_node_profile
from .relayer import plan_batch
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
