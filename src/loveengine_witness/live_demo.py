"""Anvil-backed M4 live evidence and three-process dispute review pilot."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .demo import CONTRACTS, RATE_PER_USER, deploy, free_port, start_anvil, wait_for_anvil
from .dispute import (
    aggregate_reviews,
    build_dispute,
    build_proposal_plan,
    build_review,
)
from .errors import LoveEngineError
from .hashes import keccak256_hex
from .jsonio import read_json, write_json
from .live_evidence import finalize_evidence_bundle
from .live_protocol import build_live_event, build_live_session
from .live_source import FixtureLiveSource
from .live_store import LocalArtifactStore, LiveMetadataStore
from .live_transcript import live_transcript_hash, verify_live_transcript
from .m4_network import (
    build_bootstrap_v2,
    build_node_profile_v2,
    build_task_v2,
    verify_receipt_v2,
)
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_task_v2_typed_data,
)
from .manifest import DEFAULT_MANIFEST
from .relay import RelayStore
from .relay_server import RelayHub
from .toolchain import foundry_binary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_FIXTURE = ROOT / "examples" / "live" / "live-session.fixture.ndjson"
LIVE_VERSION = "0.4.0-live-evidence-pilot"


def _rpc_sign(w3: Web3, address: str, typed_data: dict[str, Any]) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(typed_data, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


async def _run_relay_reviewers(
    *,
    output: Path,
    rpc_url: str,
    tasks: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    bootstrap: dict[str, Any],
    verdicts: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    result_dir = output / "reviews"
    profile_dir = output / "profiles"
    verdict_dir = output / "verdicts"
    store = RelayStore(output / "relay.sqlite")
    for task in tasks:
        store.enqueue(
            task["recipient"],
            task["task_id"],
            json.dumps(task, sort_keys=True),
            task["issuer"],
            task["nonce"],
        )
    hub = RelayHub(store, bootstrap, {}, {})
    port = free_port()
    await hub.start("127.0.0.1", port)
    processes: list[tuple[subprocess.Popen[str], Path]] = []
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for index, profile in enumerate(profiles, start=1):
        node = profile["profile"]["node"]
        profile_path = profile_dir / f"node-{index}.json"
        verdict_path = verdict_dir / f"node-{index}.json"
        result_path = result_dir / f"node-{index}.json"
        write_json(profile_path, profile)
        write_json(verdict_path, verdicts[node])
        expected_tasks = sum(task["recipient"] == node for task in tasks)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "loveengine_witness.live_review_node",
                "--url",
                f"http://127.0.0.1:{port}/v1/ws",
                "--rpc-url",
                rpc_url,
                "--address",
                node,
                "--profile",
                str(profile_path),
                "--expected-tasks",
                str(expected_tasks),
                "--verdicts",
                str(verdict_path),
                "--output",
                str(result_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        processes.append((process, result_path))
    try:
        failures = []
        for process, result_path in processes:
            stdout, stderr = await asyncio.to_thread(process.communicate, timeout=30)
            if process.returncode != 0:
                failures.append(
                    {
                        "returncode": process.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
            if not result_path.exists():
                failures.append({"missing_result": str(result_path)})
        if failures:
            raise LoveEngineError(
                "review_node_failed", json.dumps(failures, ensure_ascii=False), 4
            )
        clients = [read_json(path) for _, path in processes]
        if sum(len(client["receipts"]) for client in clients) != len(tasks):
            raise LoveEngineError(
                "review_receipt_count_mismatch", "Relay did not ACK every task"
            )
        metrics = hub.metrics()
        return list(hub.receipts), len(processes), metrics
    finally:
        await hub.stop()


def run_live_evidence_demo(
    output: Path,
    *,
    nodes: int = 3,
    fixture: Path = DEFAULT_LIVE_FIXTURE,
) -> dict[str, Any]:
    if nodes != 3:
        raise LoveEngineError("unsupported_node_count", "M4 pilot requires 3 nodes")
    build = subprocess.run(
        [str(foundry_binary("forge")), "build"],
        cwd=CONTRACTS,
        text=True,
        capture_output=True,
        check=False,
    )
    if build.returncode != 0:
        raise LoveEngineError("forge_build_failed", build.stderr, 4)
    output.mkdir(parents=True, exist_ok=True)

    port = free_port()
    process = start_anvil(port)
    rpc_url = f"http://127.0.0.1:{port}"
    w3 = Web3(HTTPProvider(rpc_url))
    try:
        wait_for_anvil(w3, process)
        chain_id = str(w3.eth.chain_id)
        deployer = Web3.to_checksum_address(w3.eth.accounts[0])
        corporate_admin = Web3.to_checksum_address(w3.eth.accounts[1])
        node_accounts = [
            Web3.to_checksum_address(value) for value in w3.eth.accounts[2:5]
        ]
        engine, _ = deploy(w3, deployer, "StreamingEngine", RATE_PER_USER, 10)
        public_sink, _ = deploy(w3, deployer, "PublicSink", engine.address)
        corporate, _ = deploy(
            w3, deployer, "CorporateSink", corporate_admin, 14 * 86400, 7200
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
        version_hash = Web3.keccak(text=LIVE_VERSION)
        package_hash = Web3.keccak(text="loveengine-live-evidence-pilot")
        manifest_hash = Web3.keccak(canonical_json_bytes(read_json(DEFAULT_MANIFEST)))
        registry.functions.publishRelease(
            skill_id_hash,
            version_hash,
            package_hash,
            manifest_hash,
            bytes(32),
        ).transact({"from": deployer})
        registry.functions.setCurrentVersion(skill_id_hash, version_hash).transact(
            {"from": deployer}
        )
        scheduled_at = int(w3.eth.get_block("latest")["timestamp"]) + 14 * 86400
        corporate.functions.scheduleBroadcast(
            scheduled_at, Web3.keccak(text="live-evidence-pilot")
        ).transact({"from": corporate_admin})

        valid_until = "4102444800"
        profiles = []
        for index, node in enumerate(node_accounts, start=1):
            profile = build_node_profile_v2(
                node,
                ["observe_live_text", "review_dispute"],
                str(index),
                valid_until,
            )
            signed = {
                "schema_version": "loveengine.signed-agent-node-profile/2",
                "chain_id": chain_id,
                "registry": registry.address,
                "profile": profile,
                "signature": _rpc_sign(
                    w3,
                    node,
                    build_node_profile_v2_typed_data(
                        chain_id, registry.address, profile
                    ),
                ),
            }
            profiles.append(signed)
        bootstrap = build_bootstrap_v2(
            chain_id=chain_id,
            registry=registry.address,
            publisher=deployer,
            nodes=profiles,
            sequence="1",
            valid_until=valid_until,
        )
        bootstrap["signature"] = _rpc_sign(
            w3, deployer, build_bootstrap_v2_typed_data(bootstrap)
        )

        metadata = LiveMetadataStore(output / "live.sqlite")
        artifacts = LocalArtifactStore(output / "artifacts")
        session_id = "live-evidence-session-001"
        metadata.create_session(build_live_session(session_id, "fixture", "1770000000"))
        events: list[dict[str, Any]] = []
        duplicate_count = conflict_count = 0
        for raw in FixtureLiveSource(fixture).events():
            session = metadata.get_session(session_id)
            event = build_live_event(
                event_id=raw["event_id"],
                session_id=session_id,
                sequence=session["next_sequence"],
                occurred_at=raw["occurred_at"],
                category=raw["category"],
                source_type=raw["source_type"],
                content=raw["content"],
                artifact_hash=artifacts.put(raw["content"].encode("utf-8")),
                previous_event_hash=session["head_event_hash"],
            )
            metadata.append_event(event)
            events.append(event)
        duplicate_count += int(metadata.append_event(events[0])["duplicate"])
        conflict = dict(events[0])
        conflict["content"] = "tampered"
        try:
            metadata.append_event(conflict)
        except LoveEngineError as exc:
            if exc.code == "event_conflict":
                conflict_count += 1
            else:
                raise
        session = metadata.close_session(session_id, "1770000020")
        bundle = finalize_evidence_bundle(
            metadata,
            artifacts,
            session_id,
            revision="1",
            finalized_at="1770000021",
        )

        disputes = [
            build_dispute(
                "critical-1",
                bundle["bundle_hash"],
                "critical",
                keccak256_hex(b"clarification completeness"),
                "4102444700",
            ),
            build_dispute(
                "critical-2",
                bundle["bundle_hash"],
                "critical",
                keccak256_hex(b"missing third review"),
                "4102444700",
            ),
        ]
        tasks: list[dict[str, Any]] = []

        def tasks_for(dispute: dict[str, Any], recipients: list[str]) -> list[dict[str, Any]]:
            built = []
            for node in recipients:
                task = build_task_v2(
                    chain_id=chain_id,
                    registry=registry.address,
                    task_id=f"{dispute['dispute_id']}:{node[-6:]}",
                    task_type="review_dispute",
                    issuer=deployer,
                    recipient=node,
                    manifest_hash=Web3.to_hex(manifest_hash),
                    payload={
                        "dispute_id": dispute["dispute_id"],
                        "bundle_hash": dispute["bundle_hash"],
                    },
                    nonce=str(len(tasks) + len(built) + 1),
                    deadline="4102444700",
                )
                task["signature"] = _rpc_sign(
                    w3, deployer, build_task_v2_typed_data(task)
                )
                built.append(task)
            return built

        first_tasks = tasks_for(disputes[0], node_accounts)
        tasks.extend(first_tasks)
        second_tasks = tasks_for(disputes[1], node_accounts[:2])
        tasks.extend(second_tasks)
        verdicts = {
            node_accounts[0]: {
                "critical-1": "dismiss",
                "critical-2": "dismiss",
            },
            node_accounts[1]: {
                "critical-1": "dismiss",
                "critical-2": "dismiss",
            },
            node_accounts[2]: {"critical-1": "uphold"},
        }
        receipts, node_processes, relay_metrics = asyncio.run(
            _run_relay_reviewers(
                output=output,
                rpc_url=rpc_url,
                tasks=tasks,
                profiles=profiles,
                bootstrap=bootstrap,
                verdicts=verdicts,
            )
        )
        for receipt in receipts:
            verify_receipt_v2(receipt, chain_id, registry.address)
        first_receipts = [
            receipt
            for receipt in receipts
            if receipt["result"]["dispute_id"] == "critical-1"
        ]
        second_receipts = [
            receipt
            for receipt in receipts
            if receipt["result"]["dispute_id"] == "critical-2"
        ]

        def review_values(
            dispute: dict[str, Any], selected: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            return [
                build_review(
                    receipt["task_id"],
                    dispute,
                    receipt["node"],
                    receipt["result"]["verdict"],
                    receipt["result"]["reason_hash"],
                    receipt["completed_at"],
                    receipt["signature"],
                )
                for receipt in selected
            ]

        resolved = aggregate_reviews(
            disputes[0],
            review_values(disputes[0], first_receipts),
            expected_nodes=set(node_accounts),
        )
        unresolved = aggregate_reviews(
            disputes[1],
            review_values(disputes[1], second_receipts),
            expected_nodes=set(node_accounts),
        )
        proposal = {"action": "set_total_uto", "value": "100"}
        accepted = build_proposal_plan(
            session=session,
            bundle=bundle,
            disputes=[resolved],
            proposal=proposal,
        )
        try:
            build_proposal_plan(
                session=session,
                bundle=bundle,
                disputes=[unresolved],
                proposal=proposal,
            )
        except LoveEngineError as exc:
            blocked = {"ready": False, "error": exc.code, "message": exc.message}
        else:
            raise LoveEngineError("demo_invariant_failed", "unresolved gate passed")

        transcript = {
            "schema_version": "loveengine.live-review-transcript/1",
            "run_id": "live-evidence-pilot-001",
            "chain_id": chain_id,
            "contracts": {
                "StreamingEngine": engine.address,
                "PublicSink": public_sink.address,
                "CorporateSink": corporate.address,
                "WitnessDAO": dao.address,
                "SkillRegistry": registry.address,
            },
            "bootstrap": bootstrap,
            "session": session,
            "events": events,
            "evidence_bundle": bundle,
            "nodes": profiles,
            "tasks": tasks,
            "reviews": receipts,
            "disputes": [resolved, unresolved],
            "proposal_gate": {"accepted": accepted, "blocked": blocked},
            "metrics": {
                "connected": relay_metrics["connected"],
                "queued": relay_metrics["queued"],
                "delivered": relay_metrics["delivered"],
                "acked": relay_metrics["acked"],
                "rejected": conflict_count + 1,
                "duplicate_events": duplicate_count,
                "stream_lag_seconds": 0,
                "latency_ms": relay_metrics["latency_ms"],
                "node_processes": node_processes,
            },
        }
        transcript["transcript_hash"] = live_transcript_hash(transcript)
        transcript_path = (output / "live-review.fixture.json").resolve()
        write_json(transcript_path, transcript)
        verified = verify_live_transcript(transcript)
        return {
            "session_id": session_id,
            "event_count": len(events),
            "authenticated_nodes": len(profiles),
            "review_processes": node_processes,
            "accepted_gate": verified["accepted_gate"],
            "blocked_gate": verified["blocked_gate"],
            "metrics": transcript["metrics"],
            "transcript_path": str(transcript_path),
            "transcript_hash": transcript["transcript_hash"],
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
