"""Real Anvil + HTTP/WebSocket three-node M3 network pilot."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .demo import (
    RATE_PER_USER,
    CONTRACTS,
    ROOT,
    deploy,
    free_port,
    start_anvil,
    wait_for_anvil,
)
from .errors import LoveEngineError
from .event_bridge import EventTaskBridge
from .hashes import keccak256_hex
from .jsonio import read_json, write_json
from .manifest import DEFAULT_MANIFEST
from .network_protocol import (
    build_bootstrap,
    build_node_profile,
    build_task,
    verify_task,
)
from .network_transcript import network_transcript_hash
from .network_typed_data import (
    build_bootstrap_typed_data,
    build_node_profile_typed_data,
    build_task_typed_data,
)
from .package import build_package
from .pilot_server import build_pilot_invite
from .relay import RelayStore
from .relay_server import RelayHub
from .release_identity import SKILL_VERSION
from .toolchain import foundry_binary
from .trust_policy import build_node_trust_policy


NETWORK_VERSION = SKILL_VERSION


def _rpc_sign_typed_data(
    w3: Web3,
    address: str,
    typed_data: dict[str, Any],
) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(typed_data, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


def _rpc_sign_challenge(w3: Web3, address: str, challenge: str) -> str:
    response = w3.provider.make_request(
        "eth_sign",
        [address, Web3.to_hex(text=challenge)],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


async def _run_relay_pilot(
    output: Path,
    chain_id: str,
    registry_address: str,
    release: dict[str, Any],
    artifact_path: Path,
    artifact_bytes: bytes,
    w3: Web3,
    publisher: str,
    accounts: list[str],
    chain_events: list[dict[str, Any]],
    rpc_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict]:
    valid_until = "4102444800"
    signed_profiles = []
    for index, account in enumerate(accounts):
        profile = build_node_profile(
            account,
            ["propagate_skill", "observe_broadcast"],
            str(index + 1),
            valid_until,
        )
        signed = {
            "schema_version": "loveengine.signed-agent-node-profile/1",
            "chain_id": chain_id,
            "registry": registry_address,
            "profile": profile,
            "signature": _rpc_sign_typed_data(
                w3,
                account,
                build_node_profile_typed_data(
                    chain_id,
                    registry_address,
                    profile,
                ),
            ),
        }
        signed_profiles.append(signed)

    bootstrap = build_bootstrap(
        publisher,
        signed_profiles,
        "1",
        valid_until,
    )
    bootstrap["signature"] = _rpc_sign_typed_data(
        w3,
        publisher,
        build_bootstrap_typed_data(chain_id, registry_address, bootstrap),
    )

    store = RelayStore(output / "relay.sqlite")
    tasks = []
    deadline = "4102444700"
    nonce = 0
    bridge = EventTaskBridge()
    for event in chain_events:
        event_tasks = bridge.build_tasks(
            event=event,
            registry=registry_address,
            issuer=publisher,
            recipients=accounts,
            manifest_hash=release["manifest_hash"],
            nonce_start=nonce,
            deadline=deadline,
        )
        nonce += len(event_tasks)
        for task in event_tasks:
            task["signature"] = _rpc_sign_typed_data(
                w3,
                publisher,
                build_task_typed_data(task),
            )
            tasks.append(task)
            store.enqueue(
                task["recipient"],
                task["task_id"],
                json.dumps(task, sort_keys=True),
                task["issuer"],
                task["nonce"],
            )
    event_duplicate_suppressed = (
        bridge.build_tasks(
            event=chain_events[0],
            registry=registry_address,
            issuer=publisher,
            recipients=accounts,
            manifest_hash=release["manifest_hash"],
            nonce_start=nonce,
            deadline=deadline,
        )
        == []
    )

    duplicate_suppressed = not store.enqueue(
        accounts[0],
        tasks[0]["task_id"],
        json.dumps(tasks[0], sort_keys=True),
        tasks[0]["issuer"],
        tasks[0]["nonce"],
    )
    first_delivery = store.pending(accounts[0])
    offline_redelivery = bool(first_delivery)

    key = "/".join(
        (
            publisher.lower(),
            release["skill_id"],
            release["version"],
        )
    )
    hub = RelayHub(
        store,
        bootstrap,
        {key: release},
        {release["package_hash"].lower(): artifact_bytes},
    )
    port = free_port()
    await hub.start("127.0.0.1", port)
    base_url = f"http://127.0.0.1:{port}"
    invite_path = output / "network-pilot-invite.json"
    write_json(
        invite_path,
        build_pilot_invite(
            base_url=base_url,
            chain_id=chain_id,
            registry=registry_address,
            publisher=publisher,
            version=release["version"],
            package_hash=release["package_hash"],
        ),
    )
    trust_policy_path = output / "network-pilot-trust-policy.json"
    write_json(
        trust_policy_path,
        build_node_trust_policy(
            chain_id=chain_id,
            registry=registry_address,
            publisher=publisher,
            skill_id=release["skill_id"],
            version=release["version"],
            package_hash=release["package_hash"],
            manifest_hash=release["manifest_hash"],
            allowed_issuers=[publisher],
        ),
    )
    try:
        async with ClientSession() as session:
            health = await (await session.get(base_url + "/v1/health")).json()
            fetched_bootstrap = await (
                await session.get(base_url + "/v1/bootstrap")
            ).json()
            fetched_release = await (
                await session.get(
                    base_url
                    + f"/v1/releases/{publisher}/{release['skill_id']}/{release['version']}"
                )
            ).json()
            fetched_artifact = await (
                await session.get(
                    base_url + f"/v1/artifacts/{release['package_hash']}"
                )
            ).read()
        if (
            fetched_bootstrap["directory_hash"] != bootstrap["directory_hash"]
            or fetched_release["package_hash"] != release["package_hash"]
            or fetched_artifact != artifact_bytes
            or health["status"] != "ok"
        ):
            raise LoveEngineError("relay_http_mismatch", "Relay HTTP data mismatch")

        processes = []
        result_paths = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        for index, account in enumerate(accounts):
            profile_path = output / f"node-{index + 1}.profile.json"
            result_path = output / f"node-{index + 1}.result.json"
            write_json(profile_path, signed_profiles[index])
            result_paths.append(result_path)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "loveengine_witness.cli",
                        "node",
                        "connect",
                        "--invite",
                        str(invite_path),
                        "--trust-policy",
                        str(trust_policy_path),
                        "--package",
                        str(artifact_path),
                        "--profile",
                        str(profile_path),
                        "--rpc-url",
                        rpc_url,
                        "--address",
                        account,
                        "--expected-tasks",
                        "2",
                        "--output",
                        str(result_path),
                    ],
                    cwd=Path(__file__).resolve().parents[2],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
            )
        completed = await asyncio.gather(
            *[asyncio.to_thread(process.communicate, timeout=30) for process in processes]
        )
        failures = [
            {
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
            for process, (stdout, stderr) in zip(processes, completed)
            if process.returncode != 0
        ]
        if failures:
            raise LoveEngineError(
                "node_process_failed",
                json.dumps(failures, ensure_ascii=False),
                4,
            )
        clients = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]

        for mutation in ("chain_id", "registry", "recipient"):
            tampered = copy.deepcopy(tasks[0])
            tampered[mutation] = (
                "1" if mutation == "chain_id" else "0x" + "9" * 40
            )
            try:
                verify_task(
                    tampered,
                    expected_chain_id=chain_id,
                    expected_registry=registry_address,
                    expected_recipient=accounts[0],
                )
            except LoveEngineError:
                hub.rejected += 1
        return (
            bootstrap,
            tasks,
            hub.receipts,
            {
                "clients": clients,
                "node_processes": len(processes),
                "metrics": hub.metrics(),
                "offline_redelivery": offline_redelivery,
                "duplicate_suppressed": duplicate_suppressed,
                "event_duplicate_suppressed": event_duplicate_suppressed,
            },
        )
    finally:
        await hub.stop()


def run_network_demo(output: Path, nodes: int = 3) -> dict[str, Any]:
    if nodes != 3:
        raise LoveEngineError("unsupported_node_count", "M3 pilot requires 3 nodes")
    forge = foundry_binary("forge")
    build = subprocess.run(
        [str(forge), "build"],
        cwd=CONTRACTS,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if build.returncode != 0:
        raise LoveEngineError("forge_build_failed", build.stderr, 4)

    output.mkdir(parents=True, exist_ok=True)
    package = build_package(ROOT, output / "release")
    artifact_path = package.archive
    artifact_bytes = artifact_path.read_bytes()

    port = free_port()
    process = start_anvil(port)
    w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        wait_for_anvil(w3, process)
        chain_id = str(w3.eth.chain_id)
        deployer, corporate_admin = w3.eth.accounts[0], w3.eth.accounts[1]
        engine, _ = deploy(w3, deployer, "StreamingEngine", RATE_PER_USER, 10)
        public_sink, _ = deploy(w3, deployer, "PublicSink", engine.address)
        corporate, _ = deploy(
            w3,
            deployer,
            "CorporateSink",
            corporate_admin,
            14 * 86400,
            7200,
        )
        dao, _ = deploy(
            w3,
            deployer,
            "WitnessDAO",
            engine.address,
            corporate.address,
            corporate_admin,
            3,
            9000,
        )
        registry, _ = deploy(w3, deployer, "SkillRegistry")

        skill_id_hash = Web3.keccak(text="loveengine-witness")
        version_hash = Web3.keccak(text=NETWORK_VERSION)
        package_hash = Web3.keccak(artifact_bytes)
        manifest_hash = Web3.keccak(
            canonical_json_bytes(read_json(DEFAULT_MANIFEST))
        )
        publisher = Web3.to_checksum_address(deployer)
        publish_tx = registry.functions.publishRelease(
            skill_id_hash,
            version_hash,
            package_hash,
            manifest_hash,
            bytes(32),
        ).transact({"from": publisher})
        publish_receipt = w3.eth.wait_for_transaction_receipt(publish_tx)
        registry.functions.setCurrentVersion(
            skill_id_hash,
            version_hash,
        ).transact({"from": publisher})

        scheduled_at = int(w3.eth.get_block("latest")["timestamp"]) + 14 * 86400
        schedule_tx = corporate.functions.scheduleBroadcast(
            scheduled_at,
            Web3.keccak(text="network-pilot-live"),
        ).transact({"from": corporate_admin})
        schedule_receipt = w3.eth.wait_for_transaction_receipt(schedule_tx)

        release = {
            "schema_version": "loveengine.skill-release/1",
            "chain_id": chain_id,
            "registry": registry.address,
            "publisher": publisher,
            "skill_id": "loveengine-witness",
            "version": NETWORK_VERSION,
            "version_hash": Web3.to_hex(version_hash),
            "package_hash": Web3.to_hex(package_hash),
            "manifest_hash": Web3.to_hex(manifest_hash),
            "previous_version_hash": "0x" + "0" * 64,
            "replacement_version_hash": "0x" + "0" * 64,
            "status": "active",
        }
        node_accounts = [
            Web3.to_checksum_address(value)
            for value in w3.eth.accounts[2 : 2 + nodes]
        ]
        chain_events = [
            {
                "event": "ReleasePublished",
                "chain_id": chain_id,
                "tx_hash": Web3.to_hex(publish_receipt.transactionHash),
                "log_index": "0",
                "package_hash": release["package_hash"],
            },
            {
                "event": "BroadcastScheduled",
                "chain_id": chain_id,
                "tx_hash": Web3.to_hex(schedule_receipt.transactionHash),
                "log_index": "0",
                "scheduled_at": str(scheduled_at),
            },
        ]
        bootstrap, tasks, receipts, relay_result = asyncio.run(
            _run_relay_pilot(
                output,
                chain_id,
                registry.address,
                release,
                artifact_path,
                artifact_bytes,
                w3,
                publisher,
                node_accounts,
                chain_events,
                f"http://127.0.0.1:{port}",
            )
        )

        registry.functions.setReleaseStatus(
            skill_id_hash,
            version_hash,
            2,
            bytes(32),
        ).transact({"from": publisher})
        release["status"] = "deprecated"
        transcript = {
            "schema_version": "loveengine.network-transcript/1",
            "run_id": "network-pilot-001",
            "created_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "chain_id": chain_id,
            "contracts": {
                "StreamingEngine": engine.address,
                "PublicSink": public_sink.address,
                "CorporateSink": corporate.address,
                "WitnessDAO": dao.address,
                "SkillRegistry": registry.address,
            },
            "release": release,
            "bootstrap": bootstrap,
            "nodes": bootstrap["directory"],
            "tasks": tasks,
            "receipts": receipts,
            "metrics": relay_result["metrics"],
            "node_processes": relay_result["node_processes"],
            "recovery": {
                "offline_redelivery": relay_result["offline_redelivery"],
                "duplicate_suppressed": relay_result["duplicate_suppressed"],
                "event_duplicate_suppressed": relay_result[
                    "event_duplicate_suppressed"
                ],
            },
        }
        transcript["transcript_hash"] = network_transcript_hash(transcript)
        transcript_path = (output / "network-pilot.fixture.json").resolve()
        write_json(transcript_path, transcript)
        return {
            "authenticated_nodes": len(bootstrap["directory"]),
            "node_processes": relay_result["node_processes"],
            "task_types": sorted({task["task_type"] for task in tasks}),
            "metrics": relay_result["metrics"],
            "transcript_path": str(transcript_path),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
