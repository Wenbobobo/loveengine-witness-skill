"""Machine-readable LoveEngine command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .errors import LoveEngineError
from .evidence import build_evidence_bundle
from .dispute import aggregate_reviews, build_dispute, build_proposal_plan
from .demo import run_local_loop
from .fixtures import generate_witness_fixtures
from .jsonio import read_json, write_json
from .manifest import DEFAULT_MANIFEST, verify_manifest
from .live_demo import DEFAULT_LIVE_FIXTURE, run_live_evidence_demo
from .live_evidence import finalize_evidence_bundle
from .live_gateway import serve_live
from .live_protocol import build_live_event, build_live_session
from .live_source import FixtureLiveSource
from .live_store import LocalArtifactStore, LiveMetadataStore
from .live_transcript import verify_live_transcript
from .m4_network import build_task_v2
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
from .package import (
    build_package,
    install_package,
    package_self_check,
    verify_package,
)
from .pilot_server import load_pilot_config, pilot_status, serve_pilot
from .pilot_demo import run_pilot_demo
from .pilot_transcript import verify_pilot_transcript
from .pilot_snapshot import (
    create_system_snapshot,
    prune_snapshots,
    restore_system_snapshot,
    verify_system_snapshot,
)
from .pilot_soak import run_pilot_soak
from .pilot_soak_process import background_soak_status, start_background_soak
from .pilot_chain import (
    initialize_chain,
    restore_chain,
    snapshot_chain,
    start_chain,
    status_chain,
)
from .registry import publish_plan, verify_release
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
    lan_pilot.add_argument("--events", type=int, default=12)
    lan_pilot.add_argument("--observers", type=int, default=10)
    lan_pilot.add_argument("--event-interval", type=float, default=0.01)
    lan_pilot.add_argument("--no-faults", action="store_true")
    lan_pilot.add_argument("--output", type=Path, required=True)

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

    package = commands.add_parser("package")
    package_commands = package.add_subparsers(dest="package_command")
    package_build = package_commands.add_parser("build")
    package_build.add_argument("--output", type=Path, required=True)
    package_verify = package_commands.add_parser("verify")
    package_verify.add_argument("archive", type=Path)
    package_install = package_commands.add_parser("install")
    package_install.add_argument("archive", type=Path)
    package_install.add_argument("--target", type=Path, required=True)
    package_check = package_commands.add_parser("self-check")
    package_check.add_argument("--root", type=Path, required=True)

    pilot = commands.add_parser("pilot")
    pilot_commands = pilot.add_subparsers(dest="pilot_command")
    pilot_serve = pilot_commands.add_parser("serve")
    pilot_serve.add_argument("--config", type=Path, required=True)
    pilot_status_command = pilot_commands.add_parser("status")
    pilot_status_command.add_argument("--url", required=True)
    pilot_chain = pilot_commands.add_parser("chain")
    pilot_chain_commands = pilot_chain.add_subparsers(dest="pilot_chain_command")
    chain_init = pilot_chain_commands.add_parser("init")
    chain_init.add_argument("--root", type=Path, required=True)
    chain_init.add_argument("--port", type=int, default=8545)
    chain_start = pilot_chain_commands.add_parser("start")
    chain_start.add_argument("--root", type=Path, required=True)
    chain_start.add_argument("--port", type=int, default=8545)
    chain_status = pilot_chain_commands.add_parser("status")
    chain_status.add_argument("--root", type=Path, required=True)
    chain_status.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    chain_snapshot = pilot_chain_commands.add_parser("snapshot")
    chain_snapshot.add_argument("--root", type=Path, required=True)
    chain_snapshot.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    chain_restore = pilot_chain_commands.add_parser("restore")
    chain_restore.add_argument("--root", type=Path, required=True)
    chain_restore.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    chain_restore.add_argument("--snapshot", type=Path, required=True)
    pilot_transcript = pilot_commands.add_parser("transcript")
    pilot_transcript_commands = pilot_transcript.add_subparsers(
        dest="pilot_transcript_command"
    )
    pilot_transcript_verify = pilot_transcript_commands.add_parser("verify")
    pilot_transcript_verify.add_argument("path", type=Path)
    pilot_snapshot = pilot_commands.add_parser("snapshot")
    pilot_snapshot_commands = pilot_snapshot.add_subparsers(
        dest="pilot_snapshot_command"
    )
    snapshot_create = pilot_snapshot_commands.add_parser("create")
    snapshot_create.add_argument("--config", type=Path, required=True)
    snapshot_create.add_argument("--chain-root", type=Path, required=True)
    snapshot_create.add_argument("--output", type=Path, required=True)
    snapshot_verify = pilot_snapshot_commands.add_parser("verify")
    snapshot_verify.add_argument("path", type=Path)
    snapshot_restore = pilot_snapshot_commands.add_parser("restore")
    snapshot_restore.add_argument("path", type=Path)
    snapshot_restore.add_argument("--config", type=Path, required=True)
    snapshot_restore.add_argument("--chain-root", type=Path, required=True)
    snapshot_prune = pilot_snapshot_commands.add_parser("prune")
    snapshot_prune.add_argument("--output", type=Path, required=True)
    snapshot_prune.add_argument("--older-than-days", type=int, default=30)
    pilot_soak = pilot_commands.add_parser("soak")
    pilot_soak.add_argument("--duration-seconds", type=float, default=14400)
    pilot_soak.add_argument("--events", type=int, default=240)
    pilot_soak.add_argument("--observers", type=int, default=10)
    pilot_soak.add_argument("--output", type=Path, required=True)
    pilot_soak.add_argument("--background", action="store_true")
    pilot_soak_status = pilot_commands.add_parser("soak-status")
    pilot_soak_status.add_argument("state", type=Path)

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
            event_count=args.events,
            observer_count=args.observers,
            event_interval=args.event_interval,
            simulate_faults=not args.no_faults,
        )
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
    if args.command == "package" and args.package_command == "build":
        result = build_package(Path(__file__).resolve().parents[2], args.output)
        return {
            "archive": str(result.archive.resolve()),
            "archive_sha256": result.sha256,
            "archive_keccak256": result.keccak256,
            "file_count": result.file_count,
            "checksums": str(result.checksums.resolve()),
            "sbom": str(result.sbom.resolve()),
        }
    if args.command == "package" and args.package_command == "verify":
        return verify_package(args.archive)
    if args.command == "package" and args.package_command == "install":
        return install_package(args.archive, args.target)
    if args.command == "package" and args.package_command == "self-check":
        return package_self_check(args.root)
    if args.command == "pilot" and args.pilot_command == "serve":
        serve_pilot(load_pilot_config(args.config))
        return {"stopped": True}
    if args.command == "pilot" and args.pilot_command == "status":
        return asyncio.run(pilot_status(args.url))
    if (
        args.command == "pilot"
        and args.pilot_command == "chain"
        and args.pilot_chain_command == "init"
    ):
        return initialize_chain(args.root, port=args.port)
    if (
        args.command == "pilot"
        and args.pilot_command == "chain"
        and args.pilot_chain_command == "start"
    ):
        process = start_chain(args.root, port=args.port)
        return {
            "started": True,
            "pid": process.pid,
            "rpc_url": f"http://127.0.0.1:{args.port}",
        }
    if (
        args.command == "pilot"
        and args.pilot_command == "chain"
        and args.pilot_chain_command == "status"
    ):
        return status_chain(args.root, args.rpc_url)
    if (
        args.command == "pilot"
        and args.pilot_command == "chain"
        and args.pilot_chain_command == "snapshot"
    ):
        return snapshot_chain(args.root, args.rpc_url)
    if (
        args.command == "pilot"
        and args.pilot_command == "chain"
        and args.pilot_chain_command == "restore"
    ):
        return restore_chain(args.root, args.rpc_url, args.snapshot)
    if (
        args.command == "pilot"
        and args.pilot_command == "transcript"
        and args.pilot_transcript_command == "verify"
    ):
        return verify_pilot_transcript(read_json(args.path))
    if (
        args.command == "pilot"
        and args.pilot_command == "snapshot"
        and args.pilot_snapshot_command == "create"
    ):
        config = load_pilot_config(args.config)
        return create_system_snapshot(
            run_id=config.run_id,
            database=config.database,
            relay_database=config.relay_database,
            artifact_root=config.artifact_root,
            audit_log=config.audit_log,
            chain_root=args.chain_root,
            output=args.output,
        )
    if (
        args.command == "pilot"
        and args.pilot_command == "snapshot"
        and args.pilot_snapshot_command == "verify"
    ):
        return verify_system_snapshot(args.path)
    if (
        args.command == "pilot"
        and args.pilot_command == "snapshot"
        and args.pilot_snapshot_command == "restore"
    ):
        config = load_pilot_config(args.config)
        return restore_system_snapshot(
            args.path,
            database=config.database,
            relay_database=config.relay_database,
            artifact_root=config.artifact_root,
            audit_log=config.audit_log,
            chain_root=args.chain_root,
        )
    if (
        args.command == "pilot"
        and args.pilot_command == "snapshot"
        and args.pilot_snapshot_command == "prune"
    ):
        return prune_snapshots(
            args.output, older_than_days=args.older_than_days
        )
    if args.command == "pilot" and args.pilot_command == "soak":
        if args.background:
            return start_background_soak(
                args.output,
                duration_seconds=args.duration_seconds,
                event_count=args.events,
                observers=args.observers,
            )
        return run_pilot_soak(
            args.output,
            duration_seconds=args.duration_seconds,
            event_count=args.events,
            observers=args.observers,
        )
    if args.command == "pilot" and args.pilot_command == "soak-status":
        return background_soak_status(args.state)
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
