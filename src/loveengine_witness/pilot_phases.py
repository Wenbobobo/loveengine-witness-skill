"""Observation, evidence, governance, and acceptance phases for the LAN pilot."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

from aiohttp import ClientError, ClientSession, web
from hexbytes import HexBytes
from web3 import Web3

from .core_transcript import core_transcript_hash, verify_core_transcript
from .demo import artifact, sign_typed_data, tx_summary
from .dispute import aggregate_reviews, build_dispute, build_proposal_plan, build_review
from .errors import LoveEngineError
from .hashes import keccak256_hex
from .jsonio import read_json, write_json
from .m4_network import build_task_v2
from .m4_typed_data import build_task_v2_typed_data
from .observation import aggregate_observations
from .pilot_chain import snapshot_chain
from .pilot_runtime import PilotRuntime
from .pilot_server import METRICS_KEY, RELAY_KEY, create_pilot_app
from .pilot_snapshot import create_system_snapshot
from .pilot_transcript import pilot_transcript_hash, verify_pilot_transcript
from .release_identity import SKILL_VERSION
from .typed_data import build_register_typed_data


ROOT = Path(__file__).resolve().parents[2]
VERSION = SKILL_VERSION
ZERO_HASH = "0x" + "00" * 32


@dataclass
class PilotEnvironment:
    output: Path
    w3: Web3
    deployment: dict[str, Any]
    runtime: PilotRuntime
    chain_id: str
    deployer: str
    corporate_admin: str
    node_accounts: list[str]
    witnesses: list[str]
    relayer: str
    registry: Any
    corporate: Any
    dao: Any
    public_sink: Any
    engine: Any
    transactions: list[dict[str, Any]]
    manifest_hash: str
    deadline: str
    scheduled_at: int | None
    app: web.Application
    runner: web.AppRunner
    headers: dict[str, str]
    session_id: str = "lan-pilot-session-001"
    all_processes: list[subprocess.Popen[str]] = field(default_factory=list)


@dataclass(frozen=True)
class ObservationPhase:
    hub: Any
    tasks: list[dict[str, Any]]
    receipts: list[dict[str, Any]]
    observation_set: dict[str, Any]
    observer_results: list[dict[str, int]]
    fault_recovery_seconds: float


@dataclass(frozen=True)
class EvidencePhase:
    bundle: dict[str, Any]
    live_session: dict[str, Any]
    live_events: list[dict[str, Any]]
    resolved_dispute: dict[str, Any]
    review_receipts: list[dict[str, Any]]
    proposal_gate: dict[str, Any]


@dataclass(frozen=True)
class GovernancePhase:
    proposal_id: int
    proposal_plan: dict[str, Any]
    approvals: list[dict[str, Any]]
    total_votes: int
    support_votes: int


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


def _spawn_node(arguments: list[str]) -> subprocess.Popen[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "loveengine_witness.cli", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def _runtime_trust_policy(runtime: PilotRuntime) -> tuple[dict[str, Any] | None, Path | None]:
    conventional = runtime.config.bootstrap_file.parent / "pilot-trust-policy.json"
    candidate = runtime.info.get("trust_policy_path")
    if candidate:
        path = Path(candidate).resolve()
        return read_json(path), path
    value = getattr(runtime, "trust_policy", None)
    if isinstance(value, dict):
        write_json(conventional, value)
        return value, conventional.resolve()
    if value:
        path = Path(value).resolve()
        return read_json(path), path
    if conventional.is_file():
        return read_json(conventional), conventional.resolve()
    return None, None


def _trust_policy_arguments(environment: PilotEnvironment) -> list[str]:
    _, path = _runtime_trust_policy(environment.runtime)
    return ["--trust-policy", str(path)] if path is not None else []


async def _prepare_environment(
    output: Path,
    w3: Web3,
    deployment: dict[str, Any],
    runtime: PilotRuntime,
    *,
    stage: str,
) -> PilotEnvironment:
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
    transactions: list[dict[str, Any]] = list(runtime.info["release_transactions"])
    deadline = "4102444700"

    scheduled_at = None
    if stage == "governance":
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

    package = runtime.package
    app = create_pilot_app(
        runtime.config,
        bootstrap=runtime.bootstrap,
        releases={runtime.release_key: runtime.release},
        package_artifacts={package.keccak256.lower(): package.archive.read_bytes()},
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, runtime.config.host, runtime.config.port).start()
    return PilotEnvironment(
        output=output,
        w3=w3,
        deployment=deployment,
        runtime=runtime,
        chain_id=chain_id,
        deployer=deployer,
        corporate_admin=corporate_admin,
        node_accounts=node_accounts,
        witnesses=witnesses,
        relayer=relayer,
        registry=registry,
        corporate=corporate,
        dao=dao,
        public_sink=public_sink,
        engine=engine,
        transactions=transactions,
        manifest_hash=runtime.release["manifest_hash"],
        deadline=deadline,
        scheduled_at=scheduled_at,
        app=app,
        runner=runner,
        headers={
            "Authorization": f"Bearer {runtime.config.write_token}",
            "Origin": runtime.invite["server_url"],
        },
    )


def _start_observation_processes(
    environment: PilotEnvironment,
    specs: list[tuple[int, str, Path]],
) -> list[tuple[subprocess.Popen[str], Path]]:
    started = []
    for index, node, result_path in specs:
        process = _spawn_node(
            [
                "node",
                "connect",
                "--invite",
                str(environment.output / "pilot-invite.json"),
                *_trust_policy_arguments(environment),
                "--package",
                str(environment.runtime.package.archive),
                "--rpc-url",
                str(environment.w3.provider.endpoint_uri),
                "--address",
                node,
                "--profile",
                str(environment.output / "profiles" / f"node-{index}.json"),
                "--cursor-db",
                str(environment.output / "cursors" / f"node-{index}.sqlite"),
                "--expected-tasks",
                "1",
                "--output",
                str(result_path),
            ]
        )
        environment.all_processes.append(process)
        started.append((process, result_path))
    return started


async def _enqueue_task_through_operator_api(
    environment: PilotEnvironment,
    client: ClientSession,
    task: dict[str, Any],
) -> None:
    response = await client.post(
        environment.runtime.invite["server_url"] + "/v1/relay/tasks",
        json=task,
        headers=environment.headers,
    )
    if response.status != 202:
        raise LoveEngineError(
            "pilot_task_enqueue_failed",
            f"{task['task_id']}: {await response.text()}",
        )


async def _run_observation_phase(
    environment: PilotEnvironment,
    client: ClientSession,
    *,
    event_count: int,
    observer_count: int,
    event_interval: float,
    restart_chain: Callable[[], Awaitable[None]],
    simulate_faults: bool,
) -> ObservationPhase:
    base = environment.runtime.invite["server_url"]
    response = await client.post(
        base + "/v1/live/sessions",
        json={
            "session_id": environment.session_id,
            "source_type": "operator",
            "created_at": str(int(time.time())),
        },
        headers=environment.headers,
    )
    if response.status != 201:
        raise LoveEngineError("pilot_session_create_failed", await response.text())
    observer_tasks = [
        asyncio.create_task(_read_only_observer(base, environment.session_id))
        for _ in range(observer_count)
    ]

    hub = environment.app[RELAY_KEY]
    tasks = []
    observation_specs: list[tuple[int, str, Path]] = []
    for index, node in enumerate(environment.node_accounts, start=1):
        task = build_task_v2(
            chain_id=environment.chain_id,
            registry=environment.registry.address,
            task_id=f"observe:{environment.session_id}:{index}",
            task_type="observe_live_text",
            issuer=environment.deployer,
            recipient=node,
            manifest_hash=environment.manifest_hash,
            payload={
                "schema_version": "loveengine.observe-live-text-payload/1",
                "session_id": environment.session_id,
                "stream_url": base
                + f"/v1/live/sessions/{environment.session_id}/stream",
                "session_url": base + f"/v1/live/sessions/{environment.session_id}",
                "artifact_base_url": base + "/v1/live/artifacts",
                "start_cursor": "0",
                "initial_head_hash": ZERO_HASH,
                "max_duration_seconds": min(
                    14700,
                    max(30, int(event_count * event_interval) + 180),
                ),
            },
            nonce=str(index),
            deadline=environment.deadline,
        )
        task["signature"] = _rpc_sign(
            environment.w3,
            environment.deployer,
            build_task_v2_typed_data(task),
        )
        tasks.append(task)
        await _enqueue_task_through_operator_api(environment, client, task)
        result_path = environment.output / "observations" / f"node-{index}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        observation_specs.append((index, node, result_path))

    observation_processes = _start_observation_processes(
        environment, observation_specs
    )
    fault_recovery_seconds = 0.0
    await asyncio.sleep(0.4)
    for index in range(1, event_count + 1):
        response = await client.post(
            base + f"/v1/live/sessions/{environment.session_id}/events",
            json={
                "event_id": f"lan-event-{index:04d}",
                "occurred_at": str(int(time.time())),
                "category": "source",
                "source_type": "operator",
                "content": f"LAN pilot live message {index}",
            },
            headers=environment.headers,
        )
        if response.status != 202:
            raise LoveEngineError("pilot_event_publish_failed", await response.text())
        await asyncio.sleep(event_interval)
        if simulate_faults and index == max(1, event_count // 2):
            fault_started = perf_counter()
            for process, _ in observation_processes:
                process.kill()
                process.wait(timeout=10)
            await environment.runner.cleanup()
            await restart_chain()
            package = environment.runtime.package
            environment.app = create_pilot_app(
                environment.runtime.config,
                bootstrap=environment.runtime.bootstrap,
                releases={environment.runtime.release_key: environment.runtime.release},
                package_artifacts={
                    package.keccak256.lower(): package.archive.read_bytes()
                },
            )
            environment.app[METRICS_KEY].recoveries += 1
            environment.runner = web.AppRunner(environment.app)
            await environment.runner.setup()
            await web.TCPSite(
                environment.runner,
                environment.runtime.config.host,
                environment.runtime.config.port,
            ).start()
            hub = environment.app[RELAY_KEY]
            observation_processes = _start_observation_processes(
                environment, observation_specs
            )
            await asyncio.sleep(0.4)
            fault_recovery_seconds = perf_counter() - fault_started

    response = await client.post(
        base + f"/v1/live/sessions/{environment.session_id}/close",
        json={"closed_at": str(int(time.time()))},
        headers=environment.headers,
    )
    if response.status != 200:
        raise LoveEngineError("pilot_session_close_failed", await response.text())
    response = await client.post(
        base + f"/v1/live/sessions/{environment.session_id}/evidence/finalize",
        json={"revision": "1", "finalized_at": str(int(time.time()))},
        headers=environment.headers,
    )
    if response.status != 200:
        raise LoveEngineError("pilot_evidence_finalize_failed", await response.text())
    observer_results = await asyncio.gather(*observer_tasks)
    if any(item["events"] != event_count for item in observer_results):
        raise LoveEngineError("observer_event_loss", json.dumps(observer_results))

    observation_clients = await _collect(observation_processes, "observation")
    observation_receipts = [
        receipt
        for client_result in observation_clients
        for receipt in client_result["receipts"]
    ]
    observation_set = aggregate_observations(
        observation_receipts,
        expected_nodes=set(environment.node_accounts),
        expected_chain_id=environment.chain_id,
        expected_registry=environment.registry.address,
    )
    return ObservationPhase(
        hub=hub,
        tasks=tasks,
        receipts=observation_receipts,
        observation_set=observation_set,
        observer_results=observer_results,
        fault_recovery_seconds=fault_recovery_seconds,
    )


async def _run_evidence_phase(
    environment: PilotEnvironment,
    observation: ObservationPhase,
    client: ClientSession,
) -> EvidencePhase:
    base = environment.runtime.invite["server_url"]
    async with client.get(
        base + f"/v1/live/sessions/{environment.session_id}/evidence"
    ) as response:
        bundle = await response.json()
    async with client.get(
        base + f"/v1/live/sessions/{environment.session_id}"
    ) as response:
        live_session = await response.json()
    async with client.get(
        base + f"/v1/live/sessions/{environment.session_id}/events"
    ) as response:
        live_events = (await response.json())["events"]

    dispute = build_dispute(
        "lan-critical-1",
        bundle["bundle_hash"],
        "critical",
        keccak256_hex(b"LAN pilot completeness review"),
        environment.deadline,
    )
    review_processes: list[tuple[subprocess.Popen[str], Path]] = []
    verdicts = ("dismiss", "dismiss", "uphold")
    for index, (node, verdict) in enumerate(
        zip(environment.node_accounts, verdicts, strict=True), start=1
    ):
        task = build_task_v2(
            chain_id=environment.chain_id,
            registry=environment.registry.address,
            task_id=f"review:{dispute['dispute_id']}:{index}",
            task_type="review_dispute",
            issuer=environment.deployer,
            recipient=node,
            manifest_hash=environment.manifest_hash,
            payload={
                "schema_version": "loveengine.review-dispute-payload/1",
                "dispute_id": dispute["dispute_id"],
                "bundle_hash": bundle["bundle_hash"],
                "session_id": environment.session_id,
                "evidence_url": (
                    base
                    + f"/v1/live/sessions/{environment.session_id}/evidence"
                ),
                "events_url": (
                    base
                    + f"/v1/live/sessions/{environment.session_id}/events"
                ),
                "artifact_base_url": base + "/v1/live/artifacts",
                "revision": bundle["revision"],
                "event_count": bundle["event_count"],
                "head_event_hash": bundle["head_event_hash"],
            },
            nonce=str(100 + index),
            deadline=environment.deadline,
        )
        task["signature"] = _rpc_sign(
            environment.w3,
            environment.deployer,
            build_task_v2_typed_data(task),
        )
        observation.tasks.append(task)
        await _enqueue_task_through_operator_api(environment, client, task)
        verdict_path = environment.output / "verdicts" / f"node-{index}.json"
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(verdict_path, {dispute["dispute_id"]: verdict})
        result_path = environment.output / "reviews" / f"node-{index}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        process = _spawn_node(
            [
                "node",
                "connect",
                "--invite",
                str(environment.output / "pilot-invite.json"),
                *_trust_policy_arguments(environment),
                "--package",
                str(environment.runtime.package.archive),
                "--rpc-url",
                str(environment.w3.provider.endpoint_uri),
                "--address",
                node,
                "--profile",
                str(environment.output / "profiles" / f"node-{index}.json"),
                "--verdicts",
                str(verdict_path),
                "--expected-tasks",
                "1",
                "--output",
                str(result_path),
            ]
        )
        environment.all_processes.append(process)
        review_processes.append((process, result_path))

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
        dispute, reviews, expected_nodes=set(environment.node_accounts)
    )
    gate = build_proposal_plan(
        session=live_session,
        bundle=bundle,
        disputes=[resolved],
        proposal={"action": "set_user_count", "value": "20"},
    )
    return EvidencePhase(
        bundle=bundle,
        live_session=live_session,
        live_events=live_events,
        resolved_dispute=resolved,
        review_receipts=review_receipts,
        proposal_gate=gate,
    )


def _run_governance_phase(
    environment: PilotEnvironment,
    evidence: EvidencePhase,
) -> GovernancePhase:
    w3 = environment.w3
    if environment.scheduled_at is None:
        raise LoveEngineError("governance_stage_not_prepared", "broadcast schedule")
    if not environment.corporate.functions.isProposalWindowOpen(
        int(w3.eth.get_block("latest")["timestamp"])
    ).call():
        w3.provider.make_request(
            "evm_setNextBlockTimestamp", [environment.scheduled_at + 60]
        )
        w3.provider.make_request("evm_mine", [])
    if not environment.corporate.functions.isProposalWindowOpen(
        int(w3.eth.get_block("latest")["timestamp"])
    ).call():
        raise LoveEngineError(
            "proposal_window_not_restored", str(environment.scheduled_at), 4
        )

    proposal_call = environment.dao.functions.proposeUserCount(
        20, HexBytes(evidence.bundle["bundle_hash"])
    )
    proposal_call.call({"from": environment.corporate_admin})
    w3.provider.make_request(
        "evm_setNextBlockTimestamp", [environment.scheduled_at + 120]
    )
    tx = proposal_call.transact({"from": environment.corporate_admin, "gas": 2_000_000})
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
    environment.transactions.append(
        tx_summary(proposal_receipt, "proposeUserCount")
    )
    proposal_id = int(environment.dao.functions.activeProposalId().call())
    payload_hash = Web3.to_hex(
        environment.dao.functions.proposalPayloadHash(proposal_id).call()
    )
    proposal_plan = {
        "schema_version": "loveengine.onchain-proposal-plan/1",
        "chain_id": environment.chain_id,
        "witness_dao": environment.dao.address,
        "proposal_id": str(proposal_id),
        "payload_hash": payload_hash,
        "support": True,
        "reason_hash": ZERO_HASH,
        "deadline": environment.deadline,
    }
    proposal_path = environment.output / "proposal-plan.json"
    write_json(proposal_path, proposal_plan)

    approvals = []
    for index, witness in enumerate(environment.witnesses, start=1):
        approval_path = environment.output / "votes" / f"witness-{index}.json"
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
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            raise LoveEngineError("explicit_vote_failed", process.stderr, 4)
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
    if not environment.dao.functions.proposalActive(proposal_id).call():
        raise LoveEngineError(
            "proposal_became_inactive",
            json.dumps(
                {
                    "proposal_id": proposal_id,
                    "active_proposal_id": environment.dao.functions.activeProposalId().call(),
                    "executed": environment.dao.functions.proposalExecuted(proposal_id).call(),
                    "vote_counts": environment.dao.functions.proposalVoteCounts(proposal_id).call(),
                }
            ),
            4,
        )
    tx = environment.dao.functions.batchVote(vote_tuples).transact(
        {"from": environment.relayer, "gas": 2_000_000}
    )
    environment.transactions.append(
        tx_summary(w3.eth.wait_for_transaction_receipt(tx), "batchVote")
    )
    total_votes, support_votes = environment.dao.functions.proposalVoteCounts(
        proposal_id
    ).call()
    return GovernancePhase(
        proposal_id=proposal_id,
        proposal_plan=proposal_plan,
        approvals=approvals,
        total_votes=int(total_votes),
        support_votes=int(support_votes),
    )


def _run_acceptance_phase(
    environment: PilotEnvironment,
    observation: ObservationPhase,
    evidence: EvidencePhase,
    governance: GovernancePhase | None,
    *,
    stage: str,
    event_count: int,
    observer_count: int,
    simulate_faults: bool,
) -> dict[str, Any]:
    w3 = environment.w3
    config = environment.runtime.config
    snapshots = []
    if stage == "governance":
        chain_snapshot = snapshot_chain(
            environment.output / "chain", str(w3.provider.endpoint_uri)
        )
        system_snapshot = create_system_snapshot(
            run_id=config.run_id,
            database=config.database,
            relay_database=config.relay_database,
            artifact_root=config.artifact_root,
            audit_log=config.audit_log,
            chain_root=environment.output / "chain",
            output=environment.output / "snapshots",
        )
        system_snapshot["chain_snapshot"] = chain_snapshot
        snapshots.append(system_snapshot)
    metrics = observation.hub.metrics()
    metrics["server_requests"] = environment.app[METRICS_KEY].accepted_requests
    metrics["recoveries"] = environment.app[METRICS_KEY].recoveries
    metrics["read_only_observers"] = {
        "count": observer_count,
        "connections": sum(
            item["connections"] for item in observation.observer_results
        ),
        "events_each": str(event_count),
    }
    faults = {
        "server_restarts": int(simulate_faults),
        "anvil_restarts": int(simulate_faults),
        "agent_disconnects": 3 if simulate_faults else 0,
        "recovery_seconds": round(observation.fault_recovery_seconds, 3),
    }
    artifacts = [
        {
            "artifact_hash": event["artifact_hash"],
            "size": len(event["content"].encode("utf-8")),
        }
        for event in evidence.live_events
    ]
    package = environment.runtime.package
    anchor_block = w3.eth.get_block("latest")
    anchor_number = int(anchor_block["number"])
    verification_anchor = {
        "block_number": str(anchor_number),
        "block_hash": Web3.to_hex(anchor_block["hash"]),
        "timestamp": str(anchor_block["timestamp"]),
    }
    # Verification must remain stable after the local chain advances.
    w3.provider.make_request("evm_mine", [])
    common = {
        "run_id": config.run_id,
        "version": VERSION,
        "verified_at": verification_anchor["timestamp"],
        "environment": "local_anvil",
        "actors_simulated": True,
        "verification_anchor": verification_anchor,
        "package": {
            "archive": package.archive.name,
            "archive_sha256": package.sha256,
            "archive_keccak256": package.keccak256,
            "manifest_hash": environment.manifest_hash,
        },
        "release_anchor": environment.runtime.release,
        "bootstrap": environment.runtime.bootstrap,
        "session": evidence.live_session,
        "events": evidence.live_events,
        "artifacts": artifacts,
        "network_tasks": observation.tasks,
        "observation_receipts": observation.receipts,
        "observation_set": observation.observation_set,
        "evidence_bundle": evidence.bundle,
        "dispute": evidence.resolved_dispute,
        "review_receipts": evidence.review_receipts,
        "proposal_gate": evidence.proposal_gate,
        "metrics": metrics,
        "faults": faults,
    }
    trust_policy, _ = _runtime_trust_policy(environment.runtime)
    rpc_url = str(w3.provider.endpoint_uri)

    if stage == "core":
        transcript = {
            "schema_version": "loveengine.witness-core-transcript/1",
            **common,
            "chain": {
                "chain_id": environment.chain_id,
                "registry": environment.registry.address,
                "publisher": environment.deployer,
            },
        }
        transcript["transcript_hash"] = core_transcript_hash(transcript)
        offline_verification = verify_core_transcript(transcript)
        chain_verification = verify_core_transcript(transcript, rpc_url=rpc_url)
        trust_verification = (
            verify_core_transcript(
                transcript,
                rpc_url=rpc_url,
                trust_policy=trust_policy,
            )
            if trust_policy is not None
            else None
        )
        transcript_path = environment.output / "witness-core.fixture.json"
        write_json(transcript_path, transcript)
        return {
            "stage": "core",
            "transcript": transcript,
            "transcript_path": str(transcript_path.resolve()),
            "environment": transcript["environment"],
            "actors_simulated": transcript["actors_simulated"],
            "observation_receipts": len(observation.receipts),
            "review_receipts": len(evidence.review_receipts),
            "gate_ready": bool(evidence.proposal_gate.get("ready")),
            "read_only_observers": observer_count,
            "faults": faults,
            "offline_verification": offline_verification["verification_level"],
            "chain_verification": chain_verification["verification_level"],
            "trust_verification": (
                trust_verification["verification_level"]
                if trust_verification is not None
                else None
            ),
        }

    if governance is None:
        raise LoveEngineError("governance_stage_missing", "governance result")
    transcript = {
        "schema_version": "loveengine.pilot-transcript/2",
        **common,
        "chain": {
            "chain_id": environment.chain_id,
            "registry": environment.registry.address,
            "publisher": environment.deployer,
            "witness_dao": environment.dao.address,
            "public_sink": environment.public_sink.address,
            "witnesses": environment.witnesses,
            "min_valid_votes": 5,
            "contracts": {
                name: item["address"]
                for name, item in environment.deployment["contracts"].items()
            },
        },
        "proposal": {
            **governance.proposal_plan,
            "total_votes": str(governance.total_votes),
            "support_votes": str(governance.support_votes),
        },
        "vote_approvals": governance.approvals,
        "transaction_receipts": environment.transactions,
        "contract_code_hashes": {
            name: item["code_hash"]
            for name, item in environment.deployment["contracts"].items()
        },
        "final_state": {
            "proposal_executed": environment.dao.functions.proposalExecuted(
                governance.proposal_id
            ).call(block_identifier=anchor_number),
            "rate": str(
                environment.engine.functions.rate().call(
                    block_identifier=anchor_number
                )
            ),
            "total_uto": str(
                environment.public_sink.functions.getTotalUTO().call(
                    block_identifier=anchor_number
                )
            ),
        },
        "snapshots": snapshots,
    }
    transcript["transcript_hash"] = pilot_transcript_hash(transcript)
    offline_verification = verify_pilot_transcript(transcript)
    chain_verification = verify_pilot_transcript(
        transcript, rpc_url=rpc_url
    )
    trust_verification = (
        verify_pilot_transcript(
            transcript,
            rpc_url=rpc_url,
            trust_policy=trust_policy,
        )
        if trust_policy is not None
        else None
    )
    transcript_path = environment.output / "pilot.fixture.json"
    write_json(transcript_path, transcript)
    return {
        "stage": "governance",
        "transcript": transcript,
        "transcript_path": str(transcript_path.resolve()),
        "observation_receipts": len(observation.receipts),
        "vote_approvals": len(governance.approvals),
        "proposal_executed": transcript["final_state"]["proposal_executed"],
        "total_uto": transcript["final_state"]["total_uto"],
        "read_only_observers": observer_count,
        "faults": faults,
        "offline_verification": offline_verification["verification_level"],
        "chain_verification": chain_verification["verification_level"],
        "trust_verification": (
            trust_verification["verification_level"]
            if trust_verification is not None
            else None
        ),
    }


async def run_pilot_phases(
    output: Path,
    w3: Web3,
    deployment: dict[str, Any],
    runtime: PilotRuntime,
    *,
    event_count: int,
    observer_count: int,
    event_interval: float,
    restart_chain: Callable[[], Awaitable[None]],
    simulate_faults: bool,
    stage: str = "core",
) -> dict[str, Any]:
    if stage not in {"core", "governance"}:
        raise LoveEngineError("invalid_pilot_stage", stage)
    environment = await _prepare_environment(
        output, w3, deployment, runtime, stage=stage
    )
    try:
        async with ClientSession() as client:
            observation = await _run_observation_phase(
                environment,
                client,
                event_count=event_count,
                observer_count=observer_count,
                event_interval=event_interval,
                restart_chain=restart_chain,
                simulate_faults=simulate_faults,
            )
            evidence = await _run_evidence_phase(environment, observation, client)
            governance = (
                _run_governance_phase(environment, evidence)
                if stage == "governance"
                else None
            )
            return _run_acceptance_phase(
                environment,
                observation,
                evidence,
                governance,
                stage=stage,
                event_count=event_count,
                observer_count=observer_count,
                simulate_faults=simulate_faults,
            )
    finally:
        for process in environment.all_processes:
            if process.poll() is None:
                process.kill()
        await environment.runner.cleanup()
