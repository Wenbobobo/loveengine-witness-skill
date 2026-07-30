"""CLI parser and handlers for local pilot lifecycle commands."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from .core_transcript import verify_core_transcript
from .errors import LoveEngineError
from .jsonio import read_json
from .pilot_chain import (
    initialize_chain,
    restore_chain,
    snapshot_chain,
    start_chain,
    status_chain,
)
from .pilot_server import load_pilot_config, pilot_status, quickstart_pilot, serve_pilot
from .pilot_snapshot import (
    create_system_snapshot,
    prune_snapshots,
    restore_system_snapshot,
    verify_system_snapshot,
)
from .pilot_soak import run_pilot_soak
from .pilot_soak_process import background_soak_status, start_background_soak
from .pilot_transcript import verify_pilot_transcript
from .toolchain import prepare_contracts


def add_pilot_parser(commands: Any) -> None:
    pilot = commands.add_parser("pilot")
    pilot_commands = pilot.add_subparsers(dest="pilot_command")
    quickstart = pilot_commands.add_parser("quickstart")
    quickstart.add_argument("--root", type=Path, required=True)
    quickstart.add_argument("--host", default="127.0.0.1")
    quickstart.add_argument("--port", type=int, default=8780)
    quickstart.add_argument("--rpc-port", type=int, default=8545)
    quickstart.add_argument("--base-url", default="http://127.0.0.1:8780")
    quickstart.add_argument("--headless", action="store_true")
    quickstart.add_argument("--dry-run", action="store_true")
    serve = pilot_commands.add_parser("serve")
    serve.add_argument("--config", type=Path, required=True)
    status = pilot_commands.add_parser("status")
    status.add_argument("--url", required=True)

    contracts = pilot_commands.add_parser("contracts")
    contracts_commands = contracts.add_subparsers(dest="pilot_contracts_command")
    contracts_prepare = contracts_commands.add_parser("prepare")
    contracts_prepare.add_argument("--refresh-dependencies", action="store_true")

    chain = pilot_commands.add_parser("chain")
    chain_commands = chain.add_subparsers(dest="pilot_chain_command")
    init = chain_commands.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--port", type=int, default=8545)
    start = chain_commands.add_parser("start")
    start.add_argument("--root", type=Path, required=True)
    start.add_argument("--port", type=int, default=8545)
    status_chain_parser = chain_commands.add_parser("status")
    status_chain_parser.add_argument("--root", type=Path, required=True)
    status_chain_parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    snapshot = chain_commands.add_parser("snapshot")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    restore = chain_commands.add_parser("restore")
    restore.add_argument("--root", type=Path, required=True)
    restore.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    restore.add_argument("--snapshot", type=Path, required=True)

    transcript = pilot_commands.add_parser("transcript")
    transcript_commands = transcript.add_subparsers(dest="pilot_transcript_command")
    transcript_verify = transcript_commands.add_parser("verify")
    transcript_verify.add_argument("path", type=Path)
    transcript_verify.add_argument("--rpc-url")
    transcript_verify.add_argument("--trust-policy", type=Path)

    snapshots = pilot_commands.add_parser("snapshot")
    snapshot_commands = snapshots.add_subparsers(dest="pilot_snapshot_command")
    create = snapshot_commands.add_parser("create")
    create.add_argument("--config", type=Path, required=True)
    create.add_argument("--chain-root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = snapshot_commands.add_parser("verify")
    verify.add_argument("path", type=Path)
    restore_system = snapshot_commands.add_parser("restore")
    restore_system.add_argument("path", type=Path)
    restore_system.add_argument("--config", type=Path, required=True)
    restore_system.add_argument("--chain-root", type=Path, required=True)
    prune = snapshot_commands.add_parser("prune")
    prune.add_argument("--output", type=Path, required=True)
    prune.add_argument("--older-than-days", type=int, default=30)

    soak = pilot_commands.add_parser("soak")
    soak.add_argument("--duration-seconds", type=float, default=14400)
    soak.add_argument("--events", type=int, default=240)
    soak.add_argument("--observers", type=int, default=10)
    soak.add_argument("--stage", choices=("core", "governance"), default="core")
    soak.add_argument("--output", type=Path, required=True)
    soak.add_argument("--background", action="store_true")
    soak_status = pilot_commands.add_parser("soak-status")
    soak_status.add_argument("state", type=Path)


def handle_pilot(args: argparse.Namespace) -> dict[str, Any]:
    if args.pilot_command == "quickstart":
        return quickstart_pilot(
            root=args.root,
            base_url=args.base_url,
            host=args.host,
            port=args.port,
            rpc_port=args.rpc_port,
            headless=args.headless,
            dry_run=args.dry_run,
        )
    if args.pilot_command == "serve":
        serve_pilot(load_pilot_config(args.config))
        return {"stopped": True}
    if args.pilot_command == "status":
        return asyncio.run(pilot_status(args.url))
    if (
        args.pilot_command == "contracts"
        and args.pilot_contracts_command == "prepare"
    ):
        return prepare_contracts(
            refresh_dependencies=args.refresh_dependencies,
        )
    if args.pilot_command == "chain":
        if args.pilot_chain_command == "init":
            return initialize_chain(args.root, port=args.port)
        if args.pilot_chain_command == "start":
            process = start_chain(args.root, port=args.port)
            return {
                "started": True,
                "pid": process.pid,
                "rpc_url": f"http://127.0.0.1:{args.port}",
            }
        if args.pilot_chain_command == "status":
            return status_chain(args.root, args.rpc_url)
        if args.pilot_chain_command == "snapshot":
            return snapshot_chain(args.root, args.rpc_url)
        if args.pilot_chain_command == "restore":
            return restore_chain(args.root, args.rpc_url, args.snapshot)
    if args.pilot_command == "transcript" and args.pilot_transcript_command == "verify":
        value = read_json(args.path)
        kwargs = {"rpc_url": args.rpc_url, "trust_policy": args.trust_policy}
        if value.get("schema_version") == "loveengine.witness-core-transcript/1":
            return verify_core_transcript(value, **kwargs)
        return verify_pilot_transcript(value, **kwargs)
    if args.pilot_command == "snapshot":
        if args.pilot_snapshot_command == "verify":
            return verify_system_snapshot(args.path)
        if args.pilot_snapshot_command == "prune":
            return prune_snapshots(args.output, older_than_days=args.older_than_days)
        config = load_pilot_config(args.config)
        if args.pilot_snapshot_command == "create":
            return create_system_snapshot(
                run_id=config.run_id,
                database=config.database,
                relay_database=config.relay_database,
                artifact_root=config.artifact_root,
                audit_log=config.audit_log,
                chain_root=args.chain_root,
                output=args.output,
            )
        if args.pilot_snapshot_command == "restore":
            return restore_system_snapshot(
                args.path,
                database=config.database,
                relay_database=config.relay_database,
                artifact_root=config.artifact_root,
                audit_log=config.audit_log,
                chain_root=args.chain_root,
            )
    if args.pilot_command == "soak":
        if args.background:
            return start_background_soak(
                args.output,
                duration_seconds=args.duration_seconds,
                event_count=args.events,
                observers=args.observers,
                stage=args.stage,
            )
        return run_pilot_soak(
            args.output,
            duration_seconds=args.duration_seconds,
            event_count=args.events,
            observers=args.observers,
            stage=args.stage,
        )
    if args.pilot_command == "soak-status":
        return background_soak_status(args.state)
    raise LoveEngineError("missing_command", "pilot subcommand is required")
