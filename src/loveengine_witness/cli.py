"""Machine-readable LoveEngine command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from eth_utils import to_checksum_address

from . import __version__
from .cli_pilot import add_pilot_parser, handle_pilot
from .cli_trust import (
    _build_cli_signer,
    add_clef_runtime_evidence_arguments,
    add_node_connect_parser,
    add_package_parser,
    add_registry_parser,
    add_signer_parser,
    handle_node_connect,
    handle_package,
    handle_registry,
    handle_signer,
    load_external_signer_config,
)
from .errors import LoveEngineError
from .evidence import build_evidence_bundle
from .dispute import aggregate_reviews, build_dispute, build_proposal_plan
from .demo import run_local_loop
from .fixtures import generate_witness_fixtures
from .jsonio import read_json, write_json
from .invited_operator import (
    sign_network_task,
    sign_participant_attestation,
)
from .manifest import DEFAULT_MANIFEST, verify_manifest
from .live_demo import DEFAULT_LIVE_FIXTURE, run_live_evidence_demo
from .live_evidence import finalize_evidence_bundle
from .live_gateway import serve_live
from .live_protocol import build_live_event, build_live_session
from .live_source import FixtureLiveSource
from .live_store import LocalArtifactStore, LiveMetadataStore
from .live_transcript import verify_live_transcript
from .m4_network import (
    build_bootstrap_v2,
    build_task_v2,
    verify_bootstrap_v2,
    verify_node_profile_v2,
)
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
)
from .network_demo import run_network_demo
from .network_protocol import (
    build_bootstrap,
    verify_bootstrap,
)
from .network_transcript import verify_network_transcript
from .network_typed_data import build_node_profile_typed_data
from .node_profile import build_node_profile
from .pilot_demo import run_pilot_demo
from .relayer import plan_batch
from .relay import RelayStore
from .relay_server import serve_forever
from .transcript import verify_transcript
from .typed_data import build_register_typed_data, build_vote_typed_data
from .witness_vote import approve_vote


class MachineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LoveEngineError("invalid_arguments", message)


def build_parser() -> argparse.ArgumentParser:
    parser = MachineArgumentParser(prog="loveengine")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("version")

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
    node_profile_sign.add_argument("--signer-config", type=Path)
    node_profile_sign.add_argument("--output", type=Path)
    add_clef_runtime_evidence_arguments(node_profile_sign)

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
    evidence_finalize = evidence_commands.add_parser("finalize")
    evidence_finalize.add_argument("--db", type=Path, required=True)
    evidence_finalize.add_argument("--artifacts", type=Path, required=True)
    evidence_finalize.add_argument("--session-id", required=True)
    evidence_finalize.add_argument("--revision", default="1")
    evidence_finalize.add_argument("--finalized-at", required=True)
    evidence_finalize.add_argument("--output", type=Path)

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
    live_evidence = demo_commands.add_parser("live-evidence")
    live_evidence.add_argument("--nodes", type=int, default=3)
    live_evidence.add_argument("--input", type=Path, default=DEFAULT_LIVE_FIXTURE)
    live_evidence.add_argument("--output", type=Path, required=True)
    lan_pilot = demo_commands.add_parser("lan-pilot")
    lan_pilot.add_argument("--run-id")
    lan_pilot.add_argument("--events", type=int, default=12)
    lan_pilot.add_argument("--observers", type=int, default=10)
    lan_pilot.add_argument("--event-interval", type=float, default=0.01)
    lan_pilot.add_argument("--no-faults", action="store_true")
    lan_pilot.add_argument(
        "--stage", choices=("core", "governance"), default="core"
    )
    lan_pilot.add_argument(
        "--core-transcript-version", type=int, choices=(1, 2), default=1
    )
    lan_pilot.add_argument("--acceptance-duration-seconds", type=int)
    lan_pilot.add_argument("--output", type=Path, required=True)

    add_registry_parser(commands)
    add_signer_parser(commands)

    bootstrap = commands.add_parser("bootstrap")
    bootstrap_commands = bootstrap.add_subparsers(dest="bootstrap_command")
    bootstrap_build = bootstrap_commands.add_parser("build")
    bootstrap_build.add_argument("--input", type=Path, required=True)
    bootstrap_build.add_argument("--output", type=Path)
    bootstrap_build.add_argument("--signer-config", type=Path)
    add_clef_runtime_evidence_arguments(bootstrap_build)
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

    add_node_connect_parser(node_commands)

    network = commands.add_parser("network")
    network_commands = network.add_subparsers(dest="network_command")
    network_demo = network_commands.add_parser("demo")
    network_demo.add_argument("--nodes", type=int, default=3)
    network_demo.add_argument("--output", type=Path, required=True)
    network_task = network_commands.add_parser("task")
    network_task_commands = network_task.add_subparsers(dest="network_task_command")
    network_task_sign = network_task_commands.add_parser("sign")
    network_task_sign.add_argument("--input", type=Path, required=True)
    network_task_sign.add_argument("--signer-config", type=Path, required=True)
    network_task_sign.add_argument("--trust-policy", type=Path, required=True)
    network_task_sign.add_argument("--bootstrap", type=Path, required=True)
    network_task_sign.add_argument("--output", type=Path, required=True)
    add_clef_runtime_evidence_arguments(network_task_sign)
    network_transcript = network_commands.add_parser("transcript")
    network_transcript_commands = network_transcript.add_subparsers(
        dest="network_transcript_command"
    )
    network_transcript_verify = network_transcript_commands.add_parser("verify")
    network_transcript_verify.add_argument("path", type=Path)

    participant = commands.add_parser("participant")
    participant_commands = participant.add_subparsers(dest="participant_command")
    attest = participant_commands.add_parser("attest")
    attest.add_argument("--input", type=Path, required=True)
    attest.add_argument("--signer-config", type=Path, required=True)
    attest.add_argument("--trust-policy", type=Path, required=True)
    attest.add_argument("--bootstrap", type=Path, required=True)
    attest.add_argument("--invite", type=Path, required=True)
    attest.add_argument("--assignment-task", type=Path, required=True)
    attest.add_argument("--service-config", type=Path, required=True)
    attest.add_argument("--output", type=Path, required=True)
    add_clef_runtime_evidence_arguments(attest)

    live = commands.add_parser("live")
    live_commands = live.add_subparsers(dest="live_command")
    live_serve = live_commands.add_parser("serve")
    live_serve.add_argument("--db", type=Path, required=True)
    live_serve.add_argument("--artifacts", type=Path, required=True)
    live_serve.add_argument("--host", default="127.0.0.1")
    live_serve.add_argument("--port", type=int, default=8780)
    live_session = live_commands.add_parser("session")
    live_session_commands = live_session.add_subparsers(dest="live_session_command")
    live_session_create = live_session_commands.add_parser("create")
    live_session_create.add_argument("--db", type=Path, required=True)
    live_session_create.add_argument("--session-id", required=True)
    live_session_create.add_argument("--source-type", default="fixture")
    live_session_create.add_argument("--created-at", required=True)
    live_ingest = live_commands.add_parser("ingest")
    live_ingest.add_argument("--db", type=Path, required=True)
    live_ingest.add_argument("--artifacts", type=Path, required=True)
    live_ingest.add_argument("--session-id", required=True)
    live_ingest.add_argument("--input", type=Path, required=True)
    live_close = live_commands.add_parser("close")
    live_close.add_argument("--db", type=Path, required=True)
    live_close.add_argument("--session-id", required=True)
    live_close.add_argument("--closed-at", required=True)
    live_transcript = live_commands.add_parser("transcript")
    live_transcript_commands = live_transcript.add_subparsers(
        dest="live_transcript_command"
    )
    live_transcript_verify = live_transcript_commands.add_parser("verify")
    live_transcript_verify.add_argument("path", type=Path)

    dispute = commands.add_parser("dispute")
    dispute_commands = dispute.add_subparsers(dest="dispute_command")
    dispute_open = dispute_commands.add_parser("open")
    dispute_open.add_argument("--input", type=Path, required=True)
    dispute_open.add_argument("--output", type=Path)

    review = commands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command")
    review_dispatch = review_commands.add_parser("dispatch")
    review_dispatch.add_argument("--input", type=Path, required=True)
    review_dispatch.add_argument("--output", type=Path)
    review_aggregate = review_commands.add_parser("aggregate")
    review_aggregate.add_argument("--input", type=Path, required=True)
    review_aggregate.add_argument("--output", type=Path)

    proposal = commands.add_parser("proposal")
    proposal_commands = proposal.add_subparsers(dest="proposal_command")
    proposal_gate = proposal_commands.add_parser("gate")
    proposal_gate.add_argument("--input", type=Path, required=True)
    proposal_gate.add_argument("--output", type=Path)

    add_package_parser(commands)
    add_pilot_parser(commands)

    witness = commands.add_parser("witness")
    witness_commands = witness.add_subparsers(dest="witness_command")
    witness_vote = witness_commands.add_parser("vote")
    witness_vote_commands = witness_vote.add_subparsers(dest="witness_vote_command")
    witness_approve = witness_vote_commands.add_parser("approve")
    witness_approve.add_argument("--proposal-plan", type=Path, required=True)
    witness_approve.add_argument("--rpc-url", required=True)
    witness_approve.add_argument("--address", required=True)
    witness_approve.add_argument("--output", type=Path)
    return parser


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _enforce_expected_source_commit() -> None:
    expected = os.environ.get("LOVEENGINE_EXPECTED_SOURCE_COMMIT")
    if not expected:
        return
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    actual = result.stdout.strip().lower()
    if result.returncode != 0 or actual != expected.lower():
        raise LoveEngineError(
            "source_commit_changed",
            f"expected {expected.lower()}, got {actual or 'unavailable'}",
            4,
        )


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "version":
        manifest = read_json(DEFAULT_MANIFEST)
        return {
            "package_version": __version__,
            "skill_version": manifest["version"],
            "protocol": manifest["protocol"],
            "package_root": str(Path(__file__).resolve().parents[2]),
        }
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
        if value.get("schema_version") == "loveengine.signed-agent-node-profile/2":
            typed_data = build_node_profile_v2_typed_data(
                value["chain_id"],
                value["registry"],
                value["profile"],
            )
            if args.signer_config:
                if not args.output:
                    raise LoveEngineError(
                        "invalid_arguments", "--output is required when signing", 2
                    )
                if value.get("signature", "0x") != "0x":
                    raise LoveEngineError(
                        "unsigned_input_required", "NodeProfileV2"
                    )
                config = load_external_signer_config(args.signer_config)
                if config.role != "observation_node":
                    raise LoveEngineError("wrong_signer_role", config.role)
                if config.chain_id != value["chain_id"]:
                    raise LoveEngineError("wrong_chain_id", config.chain_id)
                if config.address != to_checksum_address(value["profile"]["node"]):
                    raise LoveEngineError("wrong_signer_address", config.address)
                signer, _ = _build_cli_signer(config, args)
                value["signature"] = signer.sign_typed_data(typed_data)
                verify_node_profile_v2(
                    value,
                    expected_chain_id=value["chain_id"],
                    expected_registry=value["registry"],
                )
                write_json(args.output, value)
                return {
                    "signed": True,
                    "schema_version": value["schema_version"],
                    "node": value["profile"]["node"],
                    "output": str(args.output.resolve()),
                }
        else:
            if args.signer_config:
                raise LoveEngineError(
                    "legacy_signing_not_supported",
                    "external signer path requires NodeProfileV2",
                )
            typed_data = build_node_profile_typed_data(
                value["chain_id"],
                value["registry"],
                value["profile"],
            )
        return {
            "typed_data": typed_data,
            "signer_required": True,
        }
    if args.command == "node" and args.node_command == "connect":
        return handle_node_connect(args)
    if args.command == "fixture" and args.fixture_command == "generate":
        paths = generate_witness_fixtures(args.witnesses, args.output)
        return {"generated": len(paths), "output": str(args.output)}
    if args.command == "evidence" and args.evidence_command == "build":
        evidence = build_evidence_bundle(read_json(args.session))
        write_json(args.output, evidence)
        return evidence
    if args.command == "evidence" and args.evidence_command == "finalize":
        value = finalize_evidence_bundle(
            LiveMetadataStore(args.db),
            LocalArtifactStore(args.artifacts),
            args.session_id,
            revision=args.revision,
            finalized_at=args.finalized_at,
        )
        if args.output:
            write_json(args.output, value)
        return value
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
    if args.command == "demo" and args.demo_command == "live-evidence":
        return run_live_evidence_demo(
            args.output, nodes=args.nodes, fixture=args.input
        )
    if args.command == "demo" and args.demo_command == "lan-pilot":
        return run_pilot_demo(
            args.output,
            run_id=args.run_id,
            event_count=args.events,
            observer_count=args.observers,
            event_interval=args.event_interval,
            simulate_faults=not args.no_faults,
            stage=args.stage,
            core_transcript_version=args.core_transcript_version,
            acceptance_duration_seconds=args.acceptance_duration_seconds,
        )
    if args.command == "registry":
        return handle_registry(args)
    if args.command == "signer":
        return handle_signer(args)
    if args.command == "bootstrap" and args.bootstrap_command == "build":
        value = read_json(args.input)
        nodes = value.get("nodes", value.get("directory"))
        use_v2 = (
            value.get("schema_version") == "loveengine.bootstrap-bundle/2"
            or bool(nodes)
            and all(
                node.get("schema_version")
                == "loveengine.signed-agent-node-profile/2"
                for node in nodes
            )
        )
        if use_v2:
            bootstrap = build_bootstrap_v2(
                chain_id=value.get("chain_id", nodes[0]["chain_id"]),
                registry=value.get("registry", nodes[0]["registry"]),
                publisher=value["publisher"],
                nodes=nodes,
                sequence=value["sequence"],
                valid_until=value["valid_until"],
            )
        else:
            bootstrap = build_bootstrap(
                value["publisher"],
                nodes,
                value["sequence"],
                value["valid_until"],
            )
        bootstrap["signature"] = value.get("signature", "0x")
        if args.signer_config:
            if not use_v2:
                raise LoveEngineError(
                    "legacy_signing_not_supported",
                    "external signer path requires BootstrapV2",
                )
            if not args.output:
                raise LoveEngineError(
                    "invalid_arguments", "--output is required when signing", 2
                )
            if bootstrap["signature"] != "0x":
                raise LoveEngineError("unsigned_input_required", "BootstrapV2")
            config = load_external_signer_config(args.signer_config)
            if config.role != "publisher":
                raise LoveEngineError("wrong_signer_role", config.role)
            if config.chain_id != bootstrap["chain_id"]:
                raise LoveEngineError("wrong_chain_id", config.chain_id)
            if config.address != to_checksum_address(bootstrap["publisher"]):
                raise LoveEngineError("wrong_signer_address", config.address)
            signer, _ = _build_cli_signer(config, args)
            bootstrap["signature"] = signer.sign_typed_data(
                build_bootstrap_v2_typed_data(bootstrap)
            )
            verify_bootstrap_v2(
                bootstrap,
                bootstrap["chain_id"],
                bootstrap["registry"],
            )
        if args.output:
            write_json(args.output, bootstrap)
        if args.signer_config:
            return {
                "signed": True,
                "schema_version": bootstrap["schema_version"],
                "publisher": bootstrap["publisher"],
                "node_count": len(bootstrap["directory"]),
                "output": str(args.output.resolve()),
            }
        if bootstrap["schema_version"] == "loveengine.bootstrap-bundle/2":
            return {
                "bootstrap": bootstrap,
                "typed_data": build_bootstrap_v2_typed_data(bootstrap),
                "signer_required": bootstrap["signature"] == "0x",
            }
        return bootstrap
    if args.command == "bootstrap" and args.bootstrap_command == "verify":
        value = read_json(args.path)
        if value.get("schema_version") == "loveengine.bootstrap-bundle/2":
            verify_bootstrap_v2(value, args.chain_id, args.registry)
        else:
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
        and args.network_command == "task"
        and args.network_task_command == "sign"
    ):
        return sign_network_task(
            args.input,
            args.signer_config,
            args.output,
            trust_policy_path=args.trust_policy,
            bootstrap_path=args.bootstrap,
            ruleset_path=args.ruleset_file,
            rules_attestation_path=args.rules_attestation_file,
            clef_binary=args.clef_binary,
            expected_binary_sha256=args.expected_binary_sha256,
        )
    if (
        args.command == "network"
        and args.network_command == "transcript"
        and args.network_transcript_command == "verify"
    ):
        return verify_network_transcript(read_json(args.path))
    if args.command == "participant" and args.participant_command == "attest":
        return sign_participant_attestation(
            args.input,
            args.signer_config,
            args.output,
            trust_policy_path=args.trust_policy,
            bootstrap_path=args.bootstrap,
            invite_path=args.invite,
            assignment_task_path=args.assignment_task,
            service_config_path=args.service_config,
            ruleset_path=args.ruleset_file,
            rules_attestation_path=args.rules_attestation_file,
            clef_binary=args.clef_binary,
            expected_binary_sha256=args.expected_binary_sha256,
        )
    if args.command == "live" and args.live_command == "serve":
        serve_live(args.db, args.artifacts, args.host, args.port)
        return {"stopped": True}
    if (
        args.command == "live"
        and args.live_command == "session"
        and args.live_session_command == "create"
    ):
        return LiveMetadataStore(args.db).create_session(
            build_live_session(args.session_id, args.source_type, args.created_at)
        )
    if args.command == "live" and args.live_command == "ingest":
        metadata = LiveMetadataStore(args.db)
        artifacts = LocalArtifactStore(args.artifacts)
        accepted = duplicates = 0
        for raw in FixtureLiveSource(args.input).events():
            session = metadata.get_session(args.session_id)
            existing = metadata.get_event(raw["event_id"])
            if existing is not None and existing["content"] == raw["content"]:
                duplicates += 1
                continue
            event = build_live_event(
                event_id=raw["event_id"],
                session_id=args.session_id,
                sequence=raw.get("sequence", session["next_sequence"]),
                occurred_at=raw["occurred_at"],
                category=raw["category"],
                source_type=raw.get("source_type", session["source_type"]),
                content=raw["content"],
                artifact_hash=artifacts.put(raw["content"].encode("utf-8")),
                previous_event_hash=raw.get(
                    "previous_event_hash", session["head_event_hash"]
                ),
                source_uri=raw.get("source_uri"),
                media_url=raw.get("media_url"),
                media_hash=raw.get("media_hash"),
            )
            accepted += int(not metadata.append_event(event)["duplicate"])
        return {"accepted": accepted, "duplicates": duplicates}
    if args.command == "live" and args.live_command == "close":
        return LiveMetadataStore(args.db).close_session(
            args.session_id, args.closed_at
        )
    if (
        args.command == "live"
        and args.live_command == "transcript"
        and args.live_transcript_command == "verify"
    ):
        return verify_live_transcript(read_json(args.path))
    if args.command == "dispute" and args.dispute_command == "open":
        item = read_json(args.input)
        result = build_dispute(
            item["dispute_id"],
            item["bundle_hash"],
            item["severity"],
            item["reason_hash"],
            item["deadline"],
        )
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "review" and args.review_command == "dispatch":
        item = read_json(args.input)
        tasks = []
        for index, recipient in enumerate(item["recipients"]):
            tasks.append(
                build_task_v2(
                    chain_id=item["chain_id"],
                    registry=item["registry"],
                    task_id=f"{item['dispute']['dispute_id']}:{index + 1}",
                    task_type="review_dispute",
                    issuer=item["issuer"],
                    recipient=recipient,
                    manifest_hash=item["manifest_hash"],
                    payload={
                        "dispute_id": item["dispute"]["dispute_id"],
                        "bundle_hash": item["dispute"]["bundle_hash"],
                    },
                    nonce=str(int(item.get("nonce_start", "0")) + index),
                    deadline=item["deadline"],
                )
            )
        result = {"tasks": tasks, "signer_required": True}
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "review" and args.review_command == "aggregate":
        item = read_json(args.input)
        result = aggregate_reviews(
            item["dispute"],
            item["reviews"],
            expected_nodes=set(item["expected_nodes"]),
        )
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "proposal" and args.proposal_command == "gate":
        item = read_json(args.input)
        result = build_proposal_plan(
            session=item["session"],
            bundle=item["bundle"],
            disputes=item["disputes"],
            proposal=item["proposal"],
        )
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "package":
        return handle_package(args)
    if args.command == "pilot":
        return handle_pilot(args)
    if (
        args.command == "witness"
        and args.witness_command == "vote"
        and args.witness_vote_command == "approve"
    ):
        return approve_vote(
            args.proposal_plan,
            args.rpc_url,
            args.address,
            args.output,
        )
    raise LoveEngineError("missing_command", "a command and subcommand are required")


def main() -> int:
    parser = build_parser()
    try:
        _enforce_expected_source_commit()
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
