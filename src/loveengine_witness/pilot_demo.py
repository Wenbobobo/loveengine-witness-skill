"""Unified deterministic-package to PublicSink LAN pilot demonstration."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from time import perf_counter
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, web
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .demo import artifact, free_port, sign_typed_data, tx_summary
from .dispute import aggregate_reviews, build_dispute, build_proposal_plan, build_review
from .errors import LoveEngineError
from .hashes import keccak256_hex
from .jsonio import read_json, write_json
from .m4_network import (
    build_bootstrap_v2,
    build_node_profile_v2,
    build_task_v2,
)
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_task_v2_typed_data,
)
from .manifest import DEFAULT_MANIFEST
from .observation import aggregate_observations
from .package import build_package, install_package, package_self_check
from .pilot_chain import (
    initialize_chain,
    snapshot_chain,
    start_chain,
    status_chain,
    stop_chain,
)
from .pilot_server import (
    METRICS_KEY,
    RELAY_KEY,
    PilotConfig,
    create_pilot_app,
)
from .pilot_snapshot import create_system_snapshot
from .pilot_transcript import pilot_transcript_hash, verify_pilot_transcript
from .typed_data import build_register_typed_data
from .witness_vote import approve_vote


ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.5.0-lan-pilot"
ZERO_HASH = "0x" + "00" * 32


def _rpc_sign(w3: Web3, address: str, typed: dict[str, Any]) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(typed, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


def _contract(w3: Web3, deployment: dict[str, Any], name: str) -> Any:
    return w3.eth.contract(
        address=deployment["contracts"][name]["address"],
        abi=artifact(name)["abi"],
    )


def _spawn_node(module: str, arguments: list[str]) -> subprocess.Popen[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )


async def _collect(
    processes: list[tuple[subprocess.Popen[str], Path]], label: str
) -> list[dict[str, Any]]:
    values = []
    failures = []
    for process, result_path in processes:
        try:
            stdout, stderr = await asyncio.to_thread(process.communicate, timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            failures.append({"timeout": True, "stdout": stdout, "stderr": stderr})
            continue
        if process.returncode != 0 or not result_path.is_file():
            failures.append(
                {
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "result": str(result_path),
                }
            )
            continue
        values.append(read_json(result_path))
    if failures:
        raise LoveEngineError(
            f"{label}_node_failed", json.dumps(failures, ensure_ascii=False), 4
        )
    return values


async def _read_only_observer(base: str, session_id: str) -> dict[str, int]:
    cursor = 0
    connections = 0
    recoveries = 0
    async with ClientSession() as client:
        while True:
            try:
                async with client.get(
                    base + f"/v1/live/sessions/{session_id}/stream",
                    params={"after": str(cursor)},
                    headers={"Last-Event-ID": str(cursor)},
                ) as response:
                    body = await response.text()
                    connections += 1
                for line in body.splitlines():
                    if line.startswith("id: "):
                        cursor = max(cursor, int(line.removeprefix("id: ")))
                async with client.get(
                    base + f"/v1/live/sessions/{session_id}"
                ) as response:
                    session = await response.json()
            except (ClientError, OSError):
                recoveries += 1
                await asyncio.sleep(0.05)
                continue
            if session["status"] == "closed":
                return {
                    "events": cursor,
                    "connections": connections,
                    "recoveries": recoveries,
                }
            await asyncio.sleep(0.02)


async def _pilot_flow(
    output: Path,
    w3: Web3,
    deployment: dict[str, Any],
    package: Any,
    *,
    event_count: int,
    observer_count: int,
    event_interval: float,
    restart_chain: Any,
    simulate_faults: bool,
) -> dict[str, Any]:
    chain_id = str(w3.eth.chain_id)
    deployer = Web3.to_checksum_address(deployment["deployer"])
    corporate_admin = Web3.to_checksum_address(deployment["corporate_admin"])
    node_accounts = [
        Web3.to_checksum_address(value) for value in w3.eth.accounts[2:5]
    ]
    witnesses = [
        Web3.to_checksum_address(value) for value in deployment["witnesses"]
    ]
    relayer = Web3.to_checksum_address(deployment["relayer"])
    registry = _contract(w3, deployment, "SkillRegistry")
    corporate = _contract(w3, deployment, "CorporateSink")
    dao = _contract(w3, deployment, "WitnessDAO")
    public_sink = _contract(w3, deployment, "PublicSink")
    engine = _contract(w3, deployment, "StreamingEngine")
    transactions: list[dict[str, Any]] = []

    manifest_hash = Web3.keccak(canonical_json_bytes(read_json(DEFAULT_MANIFEST)))
    skill_id_hash = Web3.keccak(text="loveengine-witness")
    version_hash = Web3.keccak(text=VERSION)
    tx = registry.functions.publishRelease(
        skill_id_hash,
        version_hash,
        HexBytes(package.keccak256),
        manifest_hash,
        bytes(32),
    ).transact({"from": deployer})
    transactions.append(
        tx_summary(w3.eth.wait_for_transaction_receipt(tx), "publishRelease")
    )
    tx = registry.functions.setCurrentVersion(skill_id_hash, version_hash).transact(
        {"from": deployer}
    )
    transactions.append(
        tx_summary(w3.eth.wait_for_transaction_receipt(tx), "setCurrentVersion")
    )

    deadline = "4102444700"
    registrations = []
    for witness in witnesses:
        nonce = dao.functions.registerNonces(witness).call()
        typed = build_register_typed_data(
            {
                "chain_id": chain_id,
                "verifying_contract": dao.address,
                "witness": witness,
                "nonce": str(nonce),
                "deadline": deadline,
            }
        )
        v, r, s = sign_typed_data(w3, witness, typed)
        registrations.append((witness, nonce, int(deadline), v, r, s))
    tx = dao.functions.batchRegister(registrations).transact({"from": relayer})
    transactions.append(
        tx_summary(w3.eth.wait_for_transaction_receipt(tx), "batchRegister")
    )

    scheduled_at = int(w3.eth.get_block("latest")["timestamp"]) + 14 * 86400
    tx = corporate.functions.scheduleBroadcast(
        scheduled_at, Web3.keccak(text="lan-pilot-live")
    ).transact({"from": corporate_admin})
    transactions.append(
        tx_summary(w3.eth.wait_for_transaction_receipt(tx), "scheduleBroadcast")
    )
    w3.provider.make_request("evm_setNextBlockTimestamp", [scheduled_at])
    w3.provider.make_request("evm_mine", [])

    profiles = []
    profile_dir = output / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    for index, node in enumerate(node_accounts, start=1):
        profile = build_node_profile_v2(
            node, ["observe_live_text", "review_dispute"], str(index), deadline
        )
        signed = {
            "schema_version": "loveengine.signed-agent-node-profile/2",
            "chain_id": chain_id,
            "registry": registry.address,
            "profile": profile,
            "signature": _rpc_sign(
                w3,
                node,
                build_node_profile_v2_typed_data(chain_id, registry.address, profile),
            ),
        }
        profiles.append(signed)
        write_json(profile_dir / f"node-{index}.json", signed)
    bootstrap = build_bootstrap_v2(
        chain_id=chain_id,
        registry=registry.address,
        publisher=deployer,
        nodes=profiles,
        sequence="1",
        valid_until=deadline,
    )
    bootstrap["signature"] = _rpc_sign(
        w3, deployer, build_bootstrap_v2_typed_data(bootstrap)
    )

    token_file = output / "operator.token"
    token = secrets.token_urlsafe(32)
    token_file.write_text(token, encoding="utf-8")
    bootstrap_file = output / "bootstrap.json"
    write_json(bootstrap_file, bootstrap)
    release_file = output / "release.json"
    write_json(
        release_file,
        {
            "schema_version": "loveengine.skill-release/1",
            "chain_id": chain_id,
            "registry": registry.address,
            "publisher": deployer,
            "skill_id": "loveengine-witness",
            "version": VERSION,
            "version_hash": Web3.to_hex(version_hash),
            "package_hash": package.keccak256,
            "manifest_hash": Web3.to_hex(manifest_hash),
            "previous_version_hash": ZERO_HASH,
            "status": "active",
        },
    )
    server_port = free_port()
    base = f"http://127.0.0.1:{server_port}"
    config = PilotConfig(
        schema_version="loveengine.pilot-config/1",
        run_id="lan-pilot-e2e-001",
        host="127.0.0.1",
        port=server_port,
        database=output / "pilot.sqlite",
        relay_database=output / "relay.sqlite",
        artifact_root=output / "artifacts",
        audit_log=output / "audit.jsonl",
        token_file=token_file,
        bootstrap_file=bootstrap_file,
        release_file=release_file,
        package_archive=package.archive,
        allowed_origin=base,
        rpc_url=str(w3.provider.endpoint_uri),
        chain_id=chain_id,
        allow_all_interfaces=False,
        write_token=token,
    )
    app = create_pilot_app(config, bootstrap=bootstrap)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    headers = {"Authorization": f"Bearer {token}", "Origin": base}
    session_id = "lan-pilot-session-001"
    observation_processes: list[tuple[subprocess.Popen[str], Path]] = []
    review_processes: list[tuple[subprocess.Popen[str], Path]] = []
    all_processes: list[subprocess.Popen[str]] = []
    fault_recovery_seconds = 0.0
    try:
        async with ClientSession() as client:
            response = await client.post(
                base + "/v1/live/sessions",
                json={
                    "session_id": session_id,
                    "source_type": "operator",
                    "created_at": str(int(time.time())),
                },
                headers=headers,
            )
            if response.status != 201:
                raise LoveEngineError("pilot_session_create_failed", await response.text())
            observer_tasks = [
                asyncio.create_task(_read_only_observer(base, session_id))
                for _ in range(observer_count)
            ]

            hub = app[RELAY_KEY]
            tasks = []
            observation_specs: list[tuple[int, str, Path]] = []
            for index, node in enumerate(node_accounts, start=1):
                task = build_task_v2(
                    chain_id=chain_id,
                    registry=registry.address,
                    task_id=f"observe:{session_id}:{index}",
                    task_type="observe_live_text",
                    issuer=deployer,
                    recipient=node,
                    manifest_hash=Web3.to_hex(manifest_hash),
                    payload={
                        "schema_version": "loveengine.observe-live-text-payload/1",
                        "session_id": session_id,
                        "stream_url": base
                        + f"/v1/live/sessions/{session_id}/stream",
                        "session_url": base + f"/v1/live/sessions/{session_id}",
                        "artifact_base_url": base + "/v1/live/artifacts",
                        "start_cursor": "0",
                        "initial_head_hash": ZERO_HASH,
                    },
                    nonce=str(index),
                    deadline=deadline,
                )
                task["signature"] = _rpc_sign(
                    w3, deployer, build_task_v2_typed_data(task)
                )
                tasks.append(task)
                hub.store.enqueue(
                    node,
                    task["task_id"],
                    json.dumps(task, sort_keys=True),
                    deployer,
                    task["nonce"],
                )
                result_path = output / "observations" / f"node-{index}.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                observation_specs.append((index, node, result_path))

            def start_observation_processes() -> list[tuple[subprocess.Popen[str], Path]]:
                started = []
                for index, node, result_path in observation_specs:
                    process = _spawn_node(
                        "loveengine_witness.live_observation_node",
                        [
                            "--url",
                            base + "/v1/ws",
                            "--rpc-url",
                            str(w3.provider.endpoint_uri),
                            "--address",
                            node,
                            "--profile",
                            str(profile_dir / f"node-{index}.json"),
                            "--cursor-db",
                            str(output / "cursors" / f"node-{index}.sqlite"),
                            "--output",
                            str(result_path),
                        ],
                    )
                    all_processes.append(process)
                    started.append((process, result_path))
                return started

            observation_processes = start_observation_processes()
            await asyncio.sleep(0.4)
            for index in range(1, event_count + 1):
                response = await client.post(
                    base + f"/v1/live/sessions/{session_id}/events",
                    json={
                        "event_id": f"lan-event-{index:04d}",
                        "occurred_at": str(int(time.time())),
                        "category": "source",
                        "source_type": "operator",
                        "content": f"LAN pilot live message {index}",
                    },
                    headers=headers,
                )
                if response.status != 202:
                    raise LoveEngineError("pilot_event_publish_failed", await response.text())
                await asyncio.sleep(event_interval)
                if simulate_faults and index == max(1, event_count // 2):
                    fault_started = perf_counter()
                    for process, _ in observation_processes:
                        process.kill()
                        process.wait(timeout=10)
                    await runner.cleanup()
                    await restart_chain()
                    app = create_pilot_app(config, bootstrap=bootstrap)
                    app[METRICS_KEY].recoveries += 1
                    runner = web.AppRunner(app)
                    await runner.setup()
                    await web.TCPSite(runner, config.host, config.port).start()
                    hub = app[RELAY_KEY]
                    observation_processes = start_observation_processes()
                    await asyncio.sleep(0.4)
                    fault_recovery_seconds = perf_counter() - fault_started
            response = await client.post(
                base + f"/v1/live/sessions/{session_id}/close",
                json={"closed_at": str(int(time.time()))},
                headers=headers,
            )
            if response.status != 200:
                raise LoveEngineError("pilot_session_close_failed", await response.text())
            observer_results = await asyncio.gather(*observer_tasks)
            if any(item["events"] != event_count for item in observer_results):
                raise LoveEngineError(
                    "observer_event_loss", json.dumps(observer_results)
                )

            observation_clients = await _collect(
                observation_processes, "observation"
            )
            observation_receipts = [
                receipt
                for client_result in observation_clients
                for receipt in client_result["receipts"]
            ]
            observation_set = aggregate_observations(
                observation_receipts,
                expected_nodes=set(node_accounts),
                expected_chain_id=chain_id,
                expected_registry=registry.address,
            )
            async with client.get(
                base + f"/v1/live/sessions/{session_id}/evidence"
            ) as response:
                bundle = await response.json()
            async with client.get(
                base + f"/v1/live/sessions/{session_id}"
            ) as response:
                live_session = await response.json()
            async with client.get(
                base + f"/v1/live/sessions/{session_id}/events"
            ) as response:
                live_events = (await response.json())["events"]

            dispute = build_dispute(
                "lan-critical-1",
                bundle["bundle_hash"],
                "critical",
                keccak256_hex(b"LAN pilot completeness review"),
                deadline,
            )
            verdicts = ("dismiss", "dismiss", "uphold")
            for index, (node, verdict) in enumerate(
                zip(node_accounts, verdicts, strict=True), start=1
            ):
                task = build_task_v2(
                    chain_id=chain_id,
                    registry=registry.address,
                    task_id=f"review:{dispute['dispute_id']}:{index}",
                    task_type="review_dispute",
                    issuer=deployer,
                    recipient=node,
                    manifest_hash=Web3.to_hex(manifest_hash),
                    payload={
                        "dispute_id": dispute["dispute_id"],
                        "bundle_hash": bundle["bundle_hash"],
                    },
                    nonce=str(100 + index),
                    deadline=deadline,
                )
                task["signature"] = _rpc_sign(
                    w3, deployer, build_task_v2_typed_data(task)
                )
                hub.store.enqueue(
                    node,
                    task["task_id"],
                    json.dumps(task, sort_keys=True),
                    deployer,
                    task["nonce"],
                )
                verdict_path = output / "verdicts" / f"node-{index}.json"
                verdict_path.parent.mkdir(parents=True, exist_ok=True)
                write_json(verdict_path, {dispute["dispute_id"]: verdict})
                result_path = output / "reviews" / f"node-{index}.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                review_processes.append(
                    (
                        _spawn_node(
                            "loveengine_witness.live_review_node",
                            [
                                "--url",
                                base + "/v1/ws",
                                "--rpc-url",
                                str(w3.provider.endpoint_uri),
                                "--address",
                                node,
                                "--profile",
                                str(profile_dir / f"node-{index}.json"),
                                "--expected-tasks",
                                "1",
                                "--verdicts",
                                str(verdict_path),
                                "--output",
                                str(result_path),
                            ],
                        ),
                        result_path,
                    )
                )
                all_processes.append(review_processes[-1][0])
            review_clients = await _collect(review_processes, "review")
            review_receipts = [
                receipt
                for client_result in review_clients
                for receipt in client_result["receipts"]
            ]
            reviews = [
                build_review(
                    receipt["task_id"],
                    dispute,
                    receipt["node"],
                    receipt["result"]["verdict"],
                    receipt["result"]["reason_hash"],
                    receipt["completed_at"],
                    receipt["signature"],
                )
                for receipt in review_receipts
            ]
            resolved = aggregate_reviews(
                dispute, reviews, expected_nodes=set(node_accounts)
            )
            gate = build_proposal_plan(
                session=live_session,
                bundle=bundle,
                disputes=[resolved],
                proposal={"action": "set_user_count", "value": "20"},
            )

            if not corporate.functions.isProposalWindowOpen(
                int(w3.eth.get_block("latest")["timestamp"])
            ).call():
                w3.provider.make_request(
                    "evm_setNextBlockTimestamp", [scheduled_at + 60]
                )
                w3.provider.make_request("evm_mine", [])
            if not corporate.functions.isProposalWindowOpen(
                int(w3.eth.get_block("latest")["timestamp"])
            ).call():
                raise LoveEngineError(
                    "proposal_window_not_restored", str(scheduled_at), 4
                )
            proposal_call = dao.functions.proposeUserCount(
                20, HexBytes(bundle["bundle_hash"])
            )
            proposal_call.call({"from": corporate_admin})
            w3.provider.make_request(
                "evm_setNextBlockTimestamp", [scheduled_at + 120]
            )
            tx = proposal_call.transact({"from": corporate_admin, "gas": 2_000_000})
            proposal_receipt = w3.eth.wait_for_transaction_receipt(tx)
            if proposal_receipt.status != 1:
                raise LoveEngineError(
                    "proposal_transaction_failed",
                    json.dumps(
                        {
                            "transaction_hash": proposal_receipt.transactionHash.hex(),
                            "gas_used": proposal_receipt.gasUsed,
                            "block": proposal_receipt.blockNumber,
                        }
                    ),
                    4,
                )
            transactions.append(tx_summary(proposal_receipt, "proposeUserCount"))
            proposal_id = int(dao.functions.activeProposalId().call())
            payload_hash = Web3.to_hex(
                dao.functions.proposalPayloadHash(proposal_id).call()
            )
            proposal_plan = {
                "schema_version": "loveengine.onchain-proposal-plan/1",
                "chain_id": chain_id,
                "witness_dao": dao.address,
                "proposal_id": str(proposal_id),
                "payload_hash": payload_hash,
                "support": True,
                "reason_hash": ZERO_HASH,
                "deadline": deadline,
            }
            proposal_path = output / "proposal-plan.json"
            write_json(proposal_path, proposal_plan)
            approvals = []
            for index, witness in enumerate(witnesses, start=1):
                approval_path = output / "votes" / f"witness-{index}.json"
                approval_path.parent.mkdir(parents=True, exist_ok=True)
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "loveengine_witness.cli",
                        "witness",
                        "vote",
                        "approve",
                        "--proposal-plan",
                        str(proposal_path),
                        "--rpc-url",
                        str(w3.provider.endpoint_uri),
                        "--address",
                        witness,
                        "--output",
                        str(approval_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if process.returncode != 0:
                    raise LoveEngineError(
                        "explicit_vote_failed", process.stderr, 4
                    )
                approvals.append(read_json(approval_path))
            vote_tuples = [
                (
                    item["witness"],
                    int(item["proposal_id"]),
                    item["support"],
                    HexBytes(item["reason_hash"]),
                    HexBytes(item["payload_hash"]),
                    int(item["nonce"]),
                    int(item["deadline"]),
                    int(item["v"]),
                    HexBytes(item["r"]),
                    HexBytes(item["s"]),
                )
                for item in approvals
            ]
            if not dao.functions.proposalActive(proposal_id).call():
                raise LoveEngineError(
                    "proposal_became_inactive",
                    json.dumps(
                        {
                            "proposal_id": proposal_id,
                            "active_proposal_id": dao.functions.activeProposalId().call(),
                            "executed": dao.functions.proposalExecuted(
                                proposal_id
                            ).call(),
                            "vote_counts": dao.functions.proposalVoteCounts(
                                proposal_id
                            ).call(),
                        }
                    ),
                    4,
                )
            tx = dao.functions.batchVote(vote_tuples).transact(
                {"from": relayer, "gas": 2_000_000}
            )
            transactions.append(
                tx_summary(w3.eth.wait_for_transaction_receipt(tx), "batchVote")
            )
            total_votes, support_votes = dao.functions.proposalVoteCounts(
                proposal_id
            ).call()
            chain_snapshot = snapshot_chain(
                output / "chain", str(w3.provider.endpoint_uri)
            )
            system_snapshot = create_system_snapshot(
                run_id=config.run_id,
                database=config.database,
                relay_database=config.relay_database,
                artifact_root=config.artifact_root,
                audit_log=config.audit_log,
                chain_root=output / "chain",
                output=output / "snapshots",
            )
            system_snapshot["chain_snapshot"] = chain_snapshot
            metrics = hub.metrics()
            metrics["server_requests"] = app[METRICS_KEY].accepted_requests
            metrics["recoveries"] = app[METRICS_KEY].recoveries
            metrics["read_only_observers"] = {
                "count": observer_count,
                "connections": sum(
                    item["connections"] for item in observer_results
                ),
                "events_each": str(event_count),
            }
            faults = {
                "server_restarts": int(simulate_faults),
                "anvil_restarts": int(simulate_faults),
                "agent_disconnects": 3 if simulate_faults else 0,
                "recovery_seconds": round(fault_recovery_seconds, 3),
            }
            transcript = {
                "schema_version": "loveengine.pilot-transcript/1",
                "run_id": config.run_id,
                "version": VERSION,
                "package": {
                    "archive": package.archive.name,
                    "archive_sha256": package.sha256,
                    "archive_keccak256": package.keccak256,
                },
                "chain": {
                    "chain_id": chain_id,
                    "registry": registry.address,
                    "witness_dao": dao.address,
                    "public_sink": public_sink.address,
                },
                "session": live_session,
                "events": live_events,
                "observation_set": observation_set,
                "evidence_bundle": bundle,
                "dispute": resolved,
                "proposal_gate": gate,
                "proposal": {
                    **proposal_plan,
                    "total_votes": str(total_votes),
                    "support_votes": str(support_votes),
                },
                "vote_approvals": approvals,
                "transactions": transactions,
                "final_state": {
                    "proposal_executed": dao.functions.proposalExecuted(
                        proposal_id
                    ).call(),
                    "rate": str(engine.functions.rate().call()),
                    "total_uto": str(public_sink.functions.getTotalUTO().call()),
                },
                "metrics": metrics,
                "faults": faults,
                "snapshots": [system_snapshot],
            }
            transcript["transcript_hash"] = pilot_transcript_hash(transcript)
            verify_pilot_transcript(transcript)
            transcript_path = output / "pilot.fixture.json"
            write_json(transcript_path, transcript)
            return {
                "transcript": transcript,
                "transcript_path": str(transcript_path.resolve()),
                "observation_receipts": len(observation_receipts),
                "vote_approvals": len(approvals),
                "proposal_executed": transcript["final_state"]["proposal_executed"],
                "total_uto": transcript["final_state"]["total_uto"],
                "read_only_observers": observer_count,
                "faults": faults,
            }
    finally:
        for process in all_processes:
            if process.poll() is None:
                process.kill()
        await runner.cleanup()


def run_pilot_demo(
    output: Path,
    *,
    event_count: int = 12,
    observer_count: int = 10,
    event_interval: float = 0.01,
    simulate_faults: bool = True,
) -> dict[str, Any]:
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = build_package(ROOT, output / "release")
    installed = install_package(package.archive, output / "installed")
    package_self_check(output / "installed")
    chain_root = output / "chain"
    port = free_port()
    initialize_chain(chain_root, port=port)
    process_holder = {"chain": start_chain(chain_root, port=port)}
    rpc_url = f"http://127.0.0.1:{port}"
    w3 = Web3(HTTPProvider(rpc_url))

    async def restart_chain() -> None:
        snapshot_chain(chain_root, rpc_url)
        stop_chain(chain_root, process_holder["chain"])
        process_holder["chain"] = start_chain(chain_root, port=port)
        status_chain(chain_root, rpc_url)

    try:
        status_chain(chain_root, rpc_url)
        result = asyncio.run(
            _pilot_flow(
                output,
                w3,
                read_json(chain_root / "deployment.json"),
                package,
                event_count=event_count,
                observer_count=observer_count,
                event_interval=event_interval,
                restart_chain=restart_chain,
                simulate_faults=simulate_faults,
            )
        )
        result["package_installed"] = installed["installed"]
        return result
    finally:
        stop_chain(chain_root, process_holder["chain"])
