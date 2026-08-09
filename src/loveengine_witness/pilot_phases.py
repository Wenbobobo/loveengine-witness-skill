"""Observation, evidence, governance, and acceptance phases for the LAN pilot."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

from aiohttp import ClientError, ClientSession, TCPConnector, web
from hexbytes import HexBytes
from web3 import Web3

from .core_transcript import (
    core_transcript_hash,
    observe_release_anchor_rpc,
    public_service_config_hash,
    verify_core_transcript,
)
from .canonical import canonical_json_bytes
from .demo import artifact, sign_typed_data, tx_summary
from .dispute import aggregate_reviews, build_dispute, build_proposal_plan, build_review
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed
from .jsonio import read_json, write_json
from .m4_network import build_task_v2
from .m4_typed_data import build_task_v2_typed_data
from .network_typed_data import payload_hash
from .observation import aggregate_observations
from .pilot_chain import restore_chain, snapshot_chain
from .pilot_runtime import PilotRuntime
from .pilot_config import (
    PilotAdminSurfaceConfig,
    PilotConfigV2,
    PilotParticipantSurfaceConfig,
    build_pilot_invite_v2,
)
from .pilot_server import METRICS_KEY, RELAY_KEY, create_pilot_app
from .pilot_surfaces import (
    PARTICIPANT_ROUTE_ALLOWLIST,
    PilotSurfaceServer,
    create_pilot_surfaces,
)
from .pilot_snapshot import create_system_snapshot, restore_system_snapshot
from .pilot_transcript import pilot_transcript_hash, verify_pilot_transcript
from .release_identity import PROTOCOL_VERSION, SKILL_VERSION
from .participant_attestation import (
    MAX_ATTESTATION_TTL_SECONDS,
    build_participant_attestation,
    build_participant_attestation_typed_data,
)
from .review_evidence import require_verified_review_result
from .typed_data import build_register_typed_data


ROOT = Path(__file__).resolve().parents[2]
VERSION = SKILL_VERSION
ZERO_HASH = "0x" + "00" * 32
PILOT_RELAY_READY_TIMEOUT_SECONDS = 30.0
PILOT_RELAY_ACK_TIMEOUT_SECONDS = 10.0


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
    runner: web.AppRunner | None
    surface_server: PilotSurfaceServer | None
    v2_config: PilotConfigV2 | None
    hub: Any
    metrics: Any
    admin_base: str
    participant_base: str
    invite_path: Path
    core_transcript_version: int
    headers: dict[str, str]
    session_id: str = "lan-pilot-session-001"
    all_processes: list[subprocess.Popen[str]] = field(default_factory=list)
    system_restore_verified: bool = False
    system_snapshots: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ObservationPhase:
    hub: Any
    tasks: list[dict[str, Any]]
    receipts: list[dict[str, Any]]
    observation_set: dict[str, Any]
    observer_results: list[dict[str, int]]
    fault_recovery_seconds: float
    fault_disconnect_proofs: tuple[dict[str, object], ...]
    receipt_ack_loss_proofs: tuple[dict[str, object], ...]


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


def _new_pilot_client_session() -> ClientSession:
    """Avoid retaining HTTP connections across the deliberate server restart."""

    return ClientSession(connector=TCPConnector(force_close=True))


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
        if process.returncode != 0 or stderr.strip() or not result_path.is_file():
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


def _exact_source_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    value = commit.stdout.strip().lower()
    if status.returncode != 0 or status.stdout.strip():
        raise LoveEngineError(
            "transcript_source_not_clean",
            "WitnessCoreTranscriptV2 requires a clean exact source commit",
        )
    if commit.returncode != 0 or len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise LoveEngineError("source_commit_unavailable", value or "git")
    expected = os.environ.get("LOVEENGINE_EXPECTED_SOURCE_COMMIT")
    if expected and value != expected.lower():
        raise LoveEngineError(
            "source_commit_changed",
            f"expected {expected.lower()}, got {value}",
            4,
        )
    return value


def _signer_evidence_hash(kind: str, address: str, roles: list[str]) -> str:
    return sha256_prefixed(
        canonical_json_bytes(
            {
                "schema_version": "loveengine.local-signer-evidence/1",
                "backend": "anvil-test",
                "kind": kind,
                "address": Web3.to_checksum_address(address),
                "roles": roles,
            }
        )
    )


def _participant_attestation_window(anchor_timestamp: str) -> tuple[str, str]:
    issued_at = int(anchor_timestamp)
    return str(issued_at), str(issued_at + MAX_ATTESTATION_TTL_SECONDS)


def _local_v2_transcript(
    environment: PilotEnvironment,
    common: dict[str, Any],
    *,
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    if environment.v2_config is None:
        raise LoveEngineError("pilot_v2_config_required", "local V2 transcript")
    publish = next(
        (
            item
            for item in environment.transactions
            if item.get("action") == "publishRelease"
        ),
        None,
    )
    if publish is None:
        raise LoveEngineError(
            "release_transaction_missing", "local publishRelease transaction"
        )
    registry_deployment = environment.deployment["contracts"]["SkillRegistry"]
    service = {
        "schema_version": "loveengine.invited-pilot-service-config/1",
        "transport": "loopback",
        "participant_origin": environment.participant_base,
        "relay_url": environment.participant_base.replace("http://", "ws://")
        + "/v1/ws",
        "tailnet_only": False,
        "funnel_enabled": False,
        "admin_surface": "loopback_only",
        "participant_allowlist_hash": payload_hash(
            [list(item) for item in PARTICIPANT_ROUTE_ALLOWLIST]
        ),
        "tailscale_serve_config_hash": payload_hash(
            {"configured": False, "environment": "local_anvil"}
        ),
        "restore_verified": environment.system_restore_verified,
    }
    service["config_hash"] = public_service_config_hash(service)
    audit_bytes = (
        environment.runtime.config.audit_log.read_bytes()
        if environment.runtime.config.audit_log.is_file()
        else b""
    )
    audit_hash = sha256_prefixed(audit_bytes)
    pilot_invite_hash = sha256_prefixed(
        canonical_json_bytes(read_json(environment.invite_path))
    )
    trust_policy_hash = sha256_prefixed(
        canonical_json_bytes(environment.runtime.trust_policy)
    )
    publisher_roles = ["publisher", "task_issuer"]
    publisher_rules = _signer_evidence_hash(
        "ruleset", environment.deployer, publisher_roles
    )
    signer_evidence = [
        {
            "address": environment.deployer,
            "roles": publisher_roles,
            "backend": "anvil-test",
            "approval_mode": "unlocked_test",
            "ruleset_sha256": publisher_rules,
            "rules_attestation_sha256": _signer_evidence_hash(
                "rules_attestation", environment.deployer, publisher_roles
            ),
            "audit_log_sha256": audit_hash,
        }
    ]
    participant_attestations = []
    attestation_issued_at, attestation_valid_until = (
        _participant_attestation_window(common["verified_at"])
    )
    profiles = {
        Web3.to_checksum_address(item["profile"]["node"]): item["profile"]
        for item in environment.runtime.bootstrap["directory"]
    }
    for index, node in enumerate(environment.node_accounts):
        roles = ["node"]
        ruleset = _signer_evidence_hash("ruleset", node, roles)
        rules_attestation = _signer_evidence_hash(
            "rules_attestation", node, roles
        )
        assignment = next(
            task
            for task in common["network_tasks"]
            if task["task_type"] == "observe_live_text"
            and Web3.to_checksum_address(task["recipient"])
            == Web3.to_checksum_address(node)
        )
        signer_evidence.append(
            {
                "address": node,
                "roles": roles,
                "backend": "anvil-test",
                "approval_mode": "unlocked_test",
                "ruleset_sha256": ruleset,
                "rules_attestation_sha256": rules_attestation,
                "audit_log_sha256": audit_hash,
            }
        )
        attestation = build_participant_attestation(
            chain_id=environment.chain_id,
            registry=environment.registry.address,
            run_id=environment.runtime.config.run_id,
            node=node,
            role="observation_node",
            profile_hash=payload_hash(profiles[node]),
            assignment_task_id=assignment["task_id"],
            assignment_payload_hash=assignment["payload_hash"],
            pilot_invite_hash=pilot_invite_hash,
            trust_policy_hash=trust_policy_hash,
            package_hash=environment.runtime.release["package_hash"],
            manifest_hash=environment.manifest_hash,
            service_config_hash=service["config_hash"],
            ruleset_sha256=ruleset,
            rules_attestation_sha256=rules_attestation,
            operator_group_hash=payload_hash(
                {"simulated_operator_group": 1 if index < 2 else 2}
            ),
            network_group_hash=payload_hash(
                {"simulated_network_group": 1 if index == 0 else 2}
            ),
            issued_at=attestation_issued_at,
            valid_until=attestation_valid_until,
        )
        attestation["signature"] = _rpc_sign(
            environment.w3,
            node,
            build_participant_attestation_typed_data(attestation),
        )
        participant_attestations.append(attestation)
    anchor = {**common["verification_anchor"], "selected_via": "latest"}
    transcript = {
        "schema_version": "loveengine.witness-core-transcript/2",
        **common,
        "source_commit": _exact_source_commit(),
        "protocol": PROTOCOL_VERSION,
        "input_mode": "synthetic_fixture",
        "verification_anchor": anchor,
        "pilot_invite_hash": pilot_invite_hash,
        "trust_policy_hash": trust_policy_hash,
        "public_service": service,
        "signer_evidence": signer_evidence,
        "participant_attestations": participant_attestations,
        "acceptance": acceptance,
        "does_not_prove": [
            "content_truth",
            "participant_independence",
            "production_availability",
            "enterprise_identity",
            "signer_backend_or_rules_enforcement",
            "tailscale_configuration_authenticity",
        ],
        "chain": {
            "chain_id": environment.chain_id,
            "registry": environment.registry.address,
            "publisher": environment.deployer,
            "deployment_transaction": registry_deployment[
                "deployment_transaction"
            ],
            "runtime_code_hash": registry_deployment["code_hash"],
            "release_transaction": publish["transaction_hash"],
            "release_status": "active",
            "primary_rpc_observation_hash": "sha256:" + "0" * 64,
            "secondary_rpc_observation_hash": "sha256:" + "0" * 64,
        },
    }
    rpc_url = str(environment.w3.provider.endpoint_uri)
    observation_hash = observe_release_anchor_rpc(transcript, rpc_url)
    transcript["chain"]["primary_rpc_observation_hash"] = observation_hash
    transcript["chain"]["secondary_rpc_observation_hash"] = observation_hash
    transcript["transcript_hash"] = core_transcript_hash(transcript)
    return transcript


async def _read_only_observer(base: str, session_id: str) -> dict[str, int]:
    cursor = 0
    connections = 0
    recoveries = 0
    async with _new_pilot_client_session() as client:
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
                # A restart can end an SSE response after it has delivered a
                # prefix.  Session closure is terminal, so one cursor-based
                # history read closes that race without trusting a live stream.
                async with client.get(
                    base + f"/v1/live/sessions/{session_id}/events",
                    params={"after": str(cursor)},
                ) as response:
                    if response.status != 200:
                        raise LoveEngineError(
                            "observer_history_unavailable", str(response.status), 4
                        )
                    history = await response.json()
                for event in history.get("events", []):
                    cursor = max(cursor, int(event["sequence"]))
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
    core_transcript_version: int,
    participant_port: int | None,
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
    publication = {
        "bootstrap": runtime.bootstrap,
        "releases": {runtime.release_key: runtime.release},
        "package_artifacts": {
            package.keccak256.lower(): package.archive.read_bytes()
        },
    }
    admin_base = runtime.invite["server_url"]
    surface_server: PilotSurfaceServer | None = None
    v2_config: PilotConfigV2 | None = None
    if core_transcript_version == 2:
        if stage != "core" or participant_port is None:
            raise LoveEngineError(
                "invalid_core_transcript_mode", "V2 is limited to the core stage"
            )
        participant_base = f"http://127.0.0.1:{participant_port}"
        v2_config = PilotConfigV2(
            schema_version="loveengine.pilot-config/2",
            run_id=runtime.config.run_id,
            admin=PilotAdminSurfaceConfig(
                host=runtime.config.host,
                port=runtime.config.port,
                allowed_origins=(admin_base,),
            ),
            participant=PilotParticipantSurfaceConfig(
                host="127.0.0.1",
                port=participant_port,
                public_base_url=participant_base,
            ),
            database=runtime.config.database,
            relay_database=runtime.config.relay_database,
            artifact_root=runtime.config.artifact_root,
            audit_log=runtime.config.audit_log,
            token_file=runtime.config.token_file,
            bootstrap_file=runtime.config.bootstrap_file,
            release_file=runtime.config.release_file,
            package_archive=runtime.config.package_archive,
            rpc_url=runtime.config.rpc_url,
            rpc_url_file=None,
            chain_id=runtime.config.chain_id,
            write_token=runtime.config.write_token,
        )
        apps = create_pilot_surfaces(v2_config, **publication)
        surface_server = PilotSurfaceServer(v2_config, apps)
        try:
            await surface_server.start()
            app = apps.admin
            runner = None
            hub = apps.relay
            metrics = apps.metrics
            now = int(time.time())
            invite = build_pilot_invite_v2(
                participant_url=participant_base,
                chain_id=chain_id,
                registry=registry.address,
                publisher=deployer,
                version=runtime.release["version"],
                package_hash=package.keccak256,
                issued_at=str(now - 60),
                expires_at=str(now + 3600),
            )
            invite_path = output / "pilot-invite.v2.json"
            write_json(invite_path, invite)
        except BaseException:
            await surface_server.stop()
            raise
    else:
        participant_base = admin_base
        app = create_pilot_app(runtime.config, **publication)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(
            runner, runtime.config.host, runtime.config.port
        ).start()
        hub = app[RELAY_KEY]
        metrics = app[METRICS_KEY]
        invite_path = output / "pilot-invite.json"
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
        surface_server=surface_server,
        v2_config=v2_config,
        hub=hub,
        metrics=metrics,
        admin_base=admin_base,
        participant_base=participant_base,
        invite_path=invite_path,
        core_transcript_version=core_transcript_version,
        headers={
            "Authorization": f"Bearer {runtime.config.write_token}",
            "Origin": admin_base,
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
                str(environment.invite_path),
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
                "--reconnect-attempts",
                "3",
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
    async with client.post(
        environment.admin_base + "/v1/relay/tasks",
        json=task,
        headers=environment.headers,
    ) as response:
        if response.status != 202:
            raise LoveEngineError(
                "pilot_task_enqueue_failed",
                f"{task['task_id']}: {await response.text()}",
            )


async def _wait_for_relay_task_acceptance(
    hub: Any,
    *,
    node: str,
    task_id: str,
    timeout_seconds: float = PILOT_RELAY_ACK_TIMEOUT_SECONDS,
) -> None:
    """Wait until the Relay durably records this connection's task ACK."""

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        state = hub.store.task_state(node, task_id)
        if state is not None and state["accepted"]:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise LoveEngineError("pilot_task_acceptance_timeout", task_id, 4)
        await asyncio.sleep(0.05)


async def _wait_for_relay_node_connections(
    hub: Any,
    *,
    nodes: list[str],
    connected: bool,
    timeout_seconds: float = PILOT_RELAY_READY_TIMEOUT_SECONDS,
) -> None:
    """Wait for authenticated Relay membership to reach the requested state."""

    expected = {node.lower() for node in nodes}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        current = {
            str(node).lower() for node in getattr(hub, "connected", set())
        }
        reached = expected <= current if connected else expected.isdisjoint(current)
        if reached:
            return
        if asyncio.get_running_loop().time() >= deadline:
            state = "connection" if connected else "disconnect"
            raise LoveEngineError(
                f"pilot_relay_{state}_timeout", ",".join(sorted(expected)), 4
            )
        await asyncio.sleep(0.05)


async def _enqueue_task_after_relay_connection(
    environment: PilotEnvironment,
    client: ClientSession,
    hub: Any,
    *,
    task: dict[str, Any],
    node: str,
) -> None:
    """Submit a signed task only after its public node has authenticated.

    Bootstrap and release verification can take longer than task delivery. By
    separating that readiness wait from ACK latency, the Pilot proves the
    post-connection delivery path without treating process startup as an ACK.
    """

    await _wait_for_relay_node_connections(
        hub, nodes=[node], connected=True
    )
    await _enqueue_task_through_operator_api(environment, client, task)
    await _wait_for_relay_task_acceptance(
        hub, node=node, task_id=task["task_id"]
    )


async def _stop_processes(
    processes: list[tuple[subprocess.Popen[str], Path]],
) -> None:
    for process, _ in processes:
        if process.poll() is None:
            process.kill()
    for process, _ in processes:
        try:
            # ``wait`` leaves PIPE handles open. Draining them prevents the
            # Windows Proactor from closing a reset transport after asyncio's
            # managed resources have already been torn down.
            await asyncio.to_thread(process.communicate, timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise LoveEngineError(
                "pilot_node_stop_timeout", str(process.pid), 4
            ) from exc


async def _stop_pilot_server(environment: PilotEnvironment) -> None:
    try:
        if environment.surface_server is not None:
            await environment.surface_server.stop()
        elif environment.runner is not None:
            await environment.runner.cleanup()
    finally:
        environment.hub.store.close()
    # aiohttp closes non-SSL transports asynchronously. Give their callbacks
    # one loop turn before asyncio.Runner closes the Windows selector loop.
    await asyncio.sleep(0)


def _carry_restart_metrics(
    previous_hub: Any,
    previous_metrics: Any,
    next_hub: Any,
    next_metrics: Any,
) -> None:
    """Carry process-local counters across the deliberate listener restart."""

    next_hub.receipts.extend(previous_hub.receipts)
    next_hub.rejected = previous_hub.rejected
    next_hub.acceptance_latencies_ms.extend(previous_hub.acceptance_latencies_ms)
    next_hub.completion_latencies_ms.extend(previous_hub.completion_latencies_ms)
    next_hub.receipt_ack_drops.extend(previous_hub.receipt_ack_drops)
    next_hub.drop_receipt_ack_once_for.update(
        previous_hub.drop_receipt_ack_once_for
    )
    next_metrics.started_at = previous_metrics.started_at
    next_metrics.accepted_requests = previous_metrics.accepted_requests
    next_metrics.rejected_requests = previous_metrics.rejected_requests
    next_metrics.recoveries = previous_metrics.recoveries + 1


async def _restart_pilot_server(environment: PilotEnvironment) -> None:
    previous_hub = environment.hub
    previous_metrics = environment.metrics
    package = environment.runtime.package
    publication = {
        "bootstrap": environment.runtime.bootstrap,
        "releases": {
            environment.runtime.release_key: environment.runtime.release
        },
        "package_artifacts": {
            package.keccak256.lower(): package.archive.read_bytes()
        },
    }
    if environment.v2_config is not None:
        apps = create_pilot_surfaces(environment.v2_config, **publication)
        _carry_restart_metrics(
            previous_hub, previous_metrics, apps.relay, apps.metrics
        )
        server = PilotSurfaceServer(environment.v2_config, apps)
        await server.start()
        environment.app = apps.admin
        environment.runner = None
        environment.surface_server = server
        environment.hub = apps.relay
        environment.metrics = apps.metrics
        return
    environment.app = create_pilot_app(environment.runtime.config, **publication)
    _carry_restart_metrics(
        previous_hub,
        previous_metrics,
        environment.app[RELAY_KEY],
        environment.app[METRICS_KEY],
    )
    environment.runner = web.AppRunner(environment.app)
    await environment.runner.setup()
    await web.TCPSite(
        environment.runner,
        environment.runtime.config.host,
        environment.runtime.config.port,
    ).start()
    environment.surface_server = None
    environment.hub = environment.app[RELAY_KEY]
    environment.metrics = environment.app[METRICS_KEY]


def _add_snapshot_probe(database: Path, label: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE loveengine_restore_probe (label TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO loveengine_restore_probe(label) VALUES (?)", (label,)
        )
        connection.commit()


def _snapshot_probe_absent(database: Path) -> bool:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'loveengine_restore_probe'
            """
        ).fetchone()
    return row is None


def _mine_snapshot_probe_block(w3: Web3) -> int:
    before = int(w3.eth.block_number)
    response = w3.provider.make_request("evm_mine", [])
    if response.get("error") is not None:
        raise LoveEngineError(
            "snapshot_chain_probe_failed", str(response["error"]), 4
        )
    after = int(w3.eth.block_number)
    if after <= before:
        raise LoveEngineError("snapshot_chain_probe_failed", f"{before}->{after}", 4)
    return after


async def _exercise_system_snapshot_restore(
    environment: PilotEnvironment,
) -> None:
    config = environment.runtime.config
    chain_root = environment.output / "chain"
    chain_snapshot = snapshot_chain(
        chain_root, str(environment.w3.provider.endpoint_uri)
    )
    snapshot = create_system_snapshot(
        run_id=config.run_id,
        database=config.database,
        relay_database=config.relay_database,
        artifact_root=config.artifact_root,
        audit_log=config.audit_log,
        chain_root=chain_root,
        output=environment.output / "snapshots",
    )
    snapshot_path = Path(snapshot["snapshot"])
    await _stop_pilot_server(environment)

    _add_snapshot_probe(config.database, "pilot")
    _add_snapshot_probe(config.relay_database, "relay")
    artifact_probe = config.artifact_root / "loveengine-restore-probe.txt"
    artifact_probe.write_text("must disappear after restore\n", encoding="utf-8")
    with config.audit_log.open("a", encoding="utf-8") as audit:
        audit.write('{"restore_probe":true}\n')
    chain_probe_block = _mine_snapshot_probe_block(environment.w3)
    for name in ("deployment.json", "state.json"):
        path = chain_root / name
        value = read_json(path)
        value["restore_probe"] = True
        write_json(path, value)

    restored = restore_system_snapshot(
        snapshot_path,
        database=config.database,
        relay_database=config.relay_database,
        artifact_root=config.artifact_root,
        audit_log=config.audit_log,
        chain_root=chain_root,
    )
    chain_restore = restore_chain(
        chain_root,
        str(environment.w3.provider.endpoint_uri),
        Path(chain_snapshot["snapshot"]),
    )
    chain_restored = (
        chain_probe_block > int(chain_snapshot["block_number"])
        and int(environment.w3.eth.block_number) == int(chain_snapshot["block_number"])
        and int(chain_restore["block_number"]) == int(chain_snapshot["block_number"])
    )
    probes_removed = (
        _snapshot_probe_absent(config.database)
        and _snapshot_probe_absent(config.relay_database)
        and not artifact_probe.exists()
        and all(
            "restore_probe" not in read_json(chain_root / name)
            for name in ("deployment.json", "state.json")
        )
        and chain_restored
    )
    if not probes_removed:
        raise LoveEngineError(
            "snapshot_restore_probe_remaining", str(snapshot_path), 4
        )
    environment.system_restore_verified = True
    environment.system_snapshots.append(
        {
            **snapshot,
            "chain_snapshot": chain_snapshot,
            "chain_probe_block": str(chain_probe_block),
            "chain_restore": chain_restore,
            "restore": restored,
            "mutation_removed": True,
        }
    )
    await _restart_pilot_server(environment)


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
    admin_base = environment.admin_base
    base = environment.participant_base
    async with client.post(
        admin_base + "/v1/live/sessions",
        json={
            "session_id": environment.session_id,
            "source_type": "operator",
            "created_at": str(int(time.time())),
        },
        headers=environment.headers,
    ) as response:
        if response.status != 201:
            raise LoveEngineError(
                "pilot_session_create_failed", await response.text()
            )
    observer_tasks = [
        asyncio.create_task(_read_only_observer(base, environment.session_id))
        for _ in range(observer_count)
    ]

    hub = environment.hub
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
        result_path = environment.output / "observations" / f"node-{index}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        observation_specs.append((index, node, result_path))

    # The third logical witness is delayed until evidence finalization, so only
    # two observation CLI processes remain resident during the live stream.
    # A fault scenario first proves task-journal replay by interrupting its
    # accepted task before any events are published.
    delayed_spec = observation_specs[-1]
    delayed_task = tasks[-1]
    fault_disconnect_proofs: list[dict[str, object]] = []
    if simulate_faults:
        delayed_processes = _start_observation_processes(
            environment, [delayed_spec]
        )
        await _enqueue_task_after_relay_connection(
            environment,
            client,
            hub,
            task=delayed_task,
            node=delayed_spec[1],
        )
        await _stop_processes(delayed_processes)
        await _wait_for_relay_node_connections(
            hub, nodes=[delayed_spec[1]], connected=False
        )
        fault_disconnect_proofs.append(
            {
                "node": delayed_spec[1],
                "task_id": delayed_task["task_id"],
                "accepted": True,
                "connection_closed": True,
            }
        )

    active_specs = observation_specs[:-1]
    observation_processes = _start_observation_processes(environment, active_specs)
    for task, spec in zip(tasks[:-1], active_specs, strict=True):
        await _enqueue_task_after_relay_connection(
            environment, client, hub, task=task, node=spec[1]
        )
    fault_recovery_seconds = 0.0
    await asyncio.sleep(0.4)
    for index in range(1, event_count + 1):
        async with client.post(
            admin_base + f"/v1/live/sessions/{environment.session_id}/events",
            json={
                "event_id": f"lan-event-{index:04d}",
                "occurred_at": str(int(time.time())),
                "category": "source",
                "source_type": "operator",
                "content": f"LAN pilot live message {index}",
            },
            headers=environment.headers,
        ) as response:
            if response.status != 202:
                raise LoveEngineError(
                    "pilot_event_publish_failed", await response.text()
                )
        await asyncio.sleep(event_interval)
        if simulate_faults and index == max(1, event_count // 2):
            fault_started = perf_counter()
            await _stop_processes(observation_processes)
            active_nodes = [spec[1] for spec in active_specs]
            await _wait_for_relay_node_connections(
                hub, nodes=active_nodes, connected=False
            )
            fault_disconnect_proofs.extend(
                {
                    "node": spec[1],
                    "task_id": task["task_id"],
                    "accepted": True,
                    "connection_closed": True,
                }
                for task, spec in zip(tasks[:-1], active_specs, strict=True)
            )
            await _stop_pilot_server(environment)
            await restart_chain()
            await _restart_pilot_server(environment)
            hub = environment.hub
            observation_processes = _start_observation_processes(
                environment, active_specs
            )
            await asyncio.sleep(0.4)
            fault_recovery_seconds = perf_counter() - fault_started

    if simulate_faults:
        hub.drop_receipt_ack_once_for.update(task["task_id"] for task in tasks)
    async with client.post(
        admin_base + f"/v1/live/sessions/{environment.session_id}/close",
        json={"closed_at": str(int(time.time()))},
        headers=environment.headers,
    ) as response:
        if response.status != 200:
            raise LoveEngineError(
                "pilot_session_close_failed", await response.text()
            )
    async with client.post(
        admin_base + f"/v1/live/sessions/{environment.session_id}/evidence/finalize",
        json={"revision": "1", "finalized_at": str(int(time.time()))},
        headers=environment.headers,
    ) as response:
        if response.status != 200:
            raise LoveEngineError(
                "pilot_evidence_finalize_failed", await response.text()
            )
    observer_results = await asyncio.gather(*observer_tasks)
    if any(item["events"] != event_count for item in observer_results):
        raise LoveEngineError("observer_event_loss", json.dumps(observer_results))

    observation_clients = await _collect(observation_processes, "observation")
    delayed_processes = _start_observation_processes(environment, [delayed_spec])
    if not simulate_faults:
        await _enqueue_task_after_relay_connection(
            environment,
            client,
            hub,
            task=delayed_task,
            node=delayed_spec[1],
        )
    delayed_clients = await _collect(delayed_processes, "delayed_observation")
    # Review tasks reuse the observation identities. Wait until each completed
    # observation socket is gone so readiness below belongs to the new review
    # child, not a closing predecessor with the same address.
    await _wait_for_relay_node_connections(
        hub,
        nodes=list(environment.node_accounts),
        connected=False,
    )
    observation_receipts = [
        receipt
        for client_result in [*observation_clients, *delayed_clients]
        for receipt in client_result["receipts"]
    ]
    receipt_ack_loss_proofs: list[dict[str, object]] = []
    if simulate_faults:
        clients_by_node = {
            Web3.to_checksum_address(item["node"]): item
            for item in [*observation_clients, *delayed_clients]
        }
        for task in tasks:
            node = Web3.to_checksum_address(task["recipient"])
            state = hub.store.task_state(node, task["task_id"])
            client_result = clients_by_node.get(node)
            proof = {
                "node": node,
                "task_id": task["task_id"],
                "receipt_stored": bool(state and state.get("receipt")),
                "confirmation_ack_dropped": (
                    task["task_id"] in hub.receipt_ack_drops
                ),
                "receipt_state_recovered": bool(
                    client_result and int(client_result.get("reconnects", 0)) >= 1
                ),
                "receipt_confirmed": bool(
                    state and state.get("receipt_confirmed")
                ),
            }
            if not all(
                proof[field] is True
                for field in (
                    "receipt_stored",
                    "confirmation_ack_dropped",
                    "receipt_state_recovered",
                    "receipt_confirmed",
                )
            ):
                raise LoveEngineError(
                    "pilot_receipt_ack_loss_not_recovered", task["task_id"], 4
                )
            receipt_ack_loss_proofs.append(proof)
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
        fault_disconnect_proofs=tuple(fault_disconnect_proofs),
        receipt_ack_loss_proofs=tuple(receipt_ack_loss_proofs),
    )


def _reviews_from_receipts(
    dispute: dict[str, Any],
    review_receipts: list[dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    reviews = []
    for receipt in review_receipts:
        task_id = str(receipt.get("task_id", ""))
        if receipt.get("status") != "completed":
            result = receipt.get("result")
            error_code = (
                result.get("error_code", "unknown")
                if isinstance(result, dict)
                else "unknown"
            )
            raise LoveEngineError(
                "review_receipt_rejected",
                f"{task_id or 'unknown'}:{error_code}",
                4,
            )
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise LoveEngineError("review_receipt_invalid", task_id or "unknown", 4)
        task = tasks.get(task_id)
        if task is None:
            raise LoveEngineError("review_receipt_invalid", task_id or "unknown", 4)
        require_verified_review_result(
            task.get("payload"), result, task_id=task_id or "unknown"
        )
        verdict = result.get("verdict")
        reason_hash = result.get("reason_hash")
        if not isinstance(verdict, str) or not isinstance(reason_hash, str):
            raise LoveEngineError("review_receipt_invalid", task_id or "unknown", 4)
        try:
            reviews.append(
                build_review(
                    receipt["task_id"],
                    dispute,
                    receipt["node"],
                    verdict,
                    reason_hash,
                    receipt["completed_at"],
                    receipt["signature"],
                )
            )
        except (KeyError, TypeError) as exc:
            raise LoveEngineError(
                "review_receipt_invalid", task_id or "unknown", 4
            ) from exc
    return reviews


async def _run_evidence_phase(
    environment: PilotEnvironment,
    observation: ObservationPhase,
    client: ClientSession,
) -> EvidencePhase:
    base = environment.participant_base
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
    review_clients: list[dict[str, Any]] = []
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
                str(environment.invite_path),
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
        await _enqueue_task_after_relay_connection(
            environment,
            client,
            observation.hub,
            task=task,
            node=node,
        )
        review_clients.extend(
            await _collect([(process, result_path)], "review")
        )

    review_receipts = [
        receipt
        for client_result in review_clients
        for receipt in client_result["receipts"]
    ]
    reviews = _reviews_from_receipts(
        dispute,
        review_receipts,
        {task["task_id"]: task for task in observation.tasks},
    )
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
    acceptance_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    w3 = environment.w3
    config = environment.runtime.config
    snapshots = list(environment.system_snapshots)
    metrics = environment.hub.metrics()
    metrics["server_requests"] = environment.metrics.accepted_requests
    metrics["recoveries"] = environment.metrics.recoveries
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
        "agent_disconnects": len(observation.fault_disconnect_proofs),
        "agent_disconnect_proofs": list(observation.fault_disconnect_proofs),
        "receipt_ack_loss_proofs": list(observation.receipt_ack_loss_proofs),
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
        if environment.core_transcript_version == 2:
            if acceptance_profile is None:
                raise LoveEngineError(
                    "acceptance_profile_required", "WitnessCoreTranscriptV2"
                )
            transcript = _local_v2_transcript(
                environment,
                common,
                acceptance=acceptance_profile,
            )
            offline_verification = verify_core_transcript(transcript)
            secondary_rpc_url = rpc_url.replace("127.0.0.1", "localhost")
            chain_verification = verify_core_transcript(
                transcript,
                rpc_url=rpc_url,
                secondary_rpc_url=secondary_rpc_url,
            )
            trust_verification = verify_core_transcript(
                transcript,
                rpc_url=rpc_url,
                secondary_rpc_url=secondary_rpc_url,
                trust_policy=trust_policy,
            )
            transcript_path = environment.output / "witness-core-v2.fixture.json"
            write_json(transcript_path, transcript)
            return {
                "stage": "core",
                "core_transcript_version": 2,
                "transcript": transcript,
                "transcript_path": str(transcript_path.resolve()),
                "environment": transcript["environment"],
                "actors_simulated": transcript["actors_simulated"],
                "observation_receipts": len(observation.receipts),
                "review_receipts": len(evidence.review_receipts),
                "gate_ready": bool(evidence.proposal_gate.get("ready")),
                "read_only_observers": observer_count,
                "faults": faults,
                "snapshot_restore_verified": environment.system_restore_verified,
                "offline_verification": offline_verification[
                    "verification_level"
                ],
                "chain_verification": chain_verification[
                    "verification_level"
                ],
                "trust_verification": trust_verification[
                    "verification_level"
                ],
            }
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
            "core_transcript_version": 1,
            "transcript": transcript,
            "transcript_path": str(transcript_path.resolve()),
            "environment": transcript["environment"],
            "actors_simulated": transcript["actors_simulated"],
            "observation_receipts": len(observation.receipts),
            "review_receipts": len(evidence.review_receipts),
            "gate_ready": bool(evidence.proposal_gate.get("ready")),
            "read_only_observers": observer_count,
            "faults": faults,
            "snapshot_restore_verified": environment.system_restore_verified,
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
        "snapshot_restore_verified": environment.system_restore_verified,
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
    core_transcript_version: int = 1,
    participant_port: int | None = None,
    acceptance_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if stage not in {"core", "governance"}:
        raise LoveEngineError("invalid_pilot_stage", stage)
    if core_transcript_version not in {1, 2}:
        raise LoveEngineError(
            "unsupported_core_transcript_version", str(core_transcript_version)
        )
    environment = await _prepare_environment(
        output,
        w3,
        deployment,
        runtime,
        stage=stage,
        core_transcript_version=core_transcript_version,
        participant_port=participant_port,
    )
    try:
        async with _new_pilot_client_session() as client:
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
            await _exercise_system_snapshot_restore(environment)
            return _run_acceptance_phase(
                environment,
                observation,
                evidence,
                governance,
                stage=stage,
                event_count=event_count,
                observer_count=observer_count,
                simulate_faults=simulate_faults,
                acceptance_profile=acceptance_profile,
            )
    finally:
        try:
            await _stop_processes(
                [
                    (process, environment.output)
                    for process in environment.all_processes
                    if process.poll() is None
                ]
            )
        finally:
            await _stop_pilot_server(environment)
