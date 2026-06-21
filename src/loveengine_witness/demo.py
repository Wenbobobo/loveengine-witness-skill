"""Real Anvil-backed local witness loop demo."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .evidence import build_evidence_bundle
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .manifest import DEFAULT_MANIFEST
from .toolchain import foundry_binary
from .transcript import transcript_hash
from .typed_data import build_register_typed_data, build_vote_typed_data


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
RATE_PER_USER = 1_000_000_000_000


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def artifact(name: str) -> dict[str, Any]:
    return read_json(CONTRACTS / "out" / f"{name}.sol" / f"{name}.json")


def deploy(
    w3: Web3,
    sender: str,
    name: str,
    *constructor_args: Any,
) -> tuple[Any, dict[str, Any]]:
    compiled = artifact(name)
    factory = w3.eth.contract(
        abi=compiled["abi"],
        bytecode=compiled["bytecode"]["object"],
    )
    tx_hash = factory.constructor(*constructor_args).transact({"from": sender})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    contract = w3.eth.contract(
        address=receipt.contractAddress,
        abi=compiled["abi"],
    )
    return contract, receipt


def sign_typed_data(
    w3: Web3,
    account: str,
    typed_data: dict[str, Any],
) -> tuple[int, bytes, bytes]:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [account, json.dumps(typed_data, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError(
            "signer_error",
            str(response["error"]),
            4,
        )
    signature = HexBytes(response["result"])
    if len(signature) != 65:
        raise LoveEngineError("signer_error", "expected 65-byte signature", 4)
    v = signature[64]
    if v < 27:
        v += 27
    return v, bytes(signature[:32]), bytes(signature[32:64])


def tx_summary(receipt: Any, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "transaction_hash": receipt.transactionHash.hex(),
        "block_number": str(receipt.blockNumber),
        "status": str(receipt.status),
    }


def start_anvil(port: int) -> subprocess.Popen[bytes]:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        [
            str(foundry_binary("anvil")),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            "31337",
            "--silent",
        ],
        cwd=CONTRACTS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def wait_for_anvil(w3: Web3, process: subprocess.Popen[bytes]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise LoveEngineError("anvil_start_failed", "Anvil exited", 4)
        if w3.is_connected():
            return
        time.sleep(0.05)
    raise LoveEngineError("anvil_start_timeout", "Anvil did not become ready", 4)


def run_local_loop(output: Path) -> dict[str, Any]:
    forge = foundry_binary("forge")
    build = subprocess.run(
        [str(forge), "build"],
        cwd=CONTRACTS,
        text=True,
        capture_output=True,
        check=False,
    )
    if build.returncode != 0:
        raise LoveEngineError("forge_build_failed", build.stderr, 4)

    port = free_port()
    process = start_anvil(port)
    w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        wait_for_anvil(w3, process)
        accounts = [Web3.to_checksum_address(item) for item in w3.eth.accounts]
        deployer, corporate_admin = accounts[0], accounts[1]
        witnesses = accounts[2:7]
        relayer = accounts[7]
        events: list[dict[str, Any]] = []

        engine, receipt = deploy(
            w3,
            deployer,
            "StreamingEngine",
            RATE_PER_USER,
            10,
        )
        events.append(tx_summary(receipt, "deploy StreamingEngine"))
        public_sink, receipt = deploy(
            w3,
            deployer,
            "PublicSink",
            engine.address,
        )
        events.append(tx_summary(receipt, "deploy PublicSink"))
        corporate, receipt = deploy(
            w3,
            deployer,
            "CorporateSink",
            corporate_admin,
            14 * 24 * 60 * 60,
            2 * 60 * 60,
        )
        events.append(tx_summary(receipt, "deploy CorporateSink"))
        dao, receipt = deploy(
            w3,
            deployer,
            "WitnessDAO",
            engine.address,
            corporate.address,
            corporate_admin,
            5,
            9000,
        )
        events.append(tx_summary(receipt, "deploy WitnessDAO"))

        for contract, action in (
            (engine, "bind StreamingEngine"),
            (corporate, "bind CorporateSink"),
        ):
            tx_hash = contract.functions.setWitnessDAO(dao.address).transact(
                {"from": deployer}
            )
            events.append(
                tx_summary(w3.eth.wait_for_transaction_receipt(tx_hash), action)
            )

        latest = w3.eth.get_block("latest")
        deadline = int(latest["timestamp"]) + 3600
        registrations = []
        for witness in witnesses:
            nonce = dao.functions.registerNonces(witness).call()
            typed = build_register_typed_data(
                {
                    "chain_id": str(w3.eth.chain_id),
                    "verifying_contract": dao.address,
                    "witness": witness,
                    "nonce": str(nonce),
                    "deadline": str(deadline),
                }
            )
            v, r, s = sign_typed_data(w3, witness, typed)
            registrations.append((witness, nonce, deadline, v, r, s))
        tx_hash = dao.functions.batchRegister(registrations).transact(
            {"from": relayer}
        )
        events.append(
            tx_summary(
                w3.eth.wait_for_transaction_receipt(tx_hash),
                "batchRegister",
            )
        )

        scheduled_at = int(w3.eth.get_block("latest")["timestamp"]) + 14 * 86400
        tx_hash = corporate.functions.scheduleBroadcast(
            scheduled_at,
            Web3.keccak(text="local-loop-live"),
        ).transact({"from": corporate_admin})
        events.append(
            tx_summary(
                w3.eth.wait_for_transaction_receipt(tx_hash),
                "scheduleBroadcast",
            )
        )
        w3.provider.make_request("evm_setNextBlockTimestamp", [scheduled_at])
        w3.provider.make_request("evm_mine", [])

        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        evidence = build_evidence_bundle(
            {
                "bundle_id": "local-loop-evidence-001",
                "session_id": "local-loop-session-001",
                "proposal_type": "USER_COUNT",
                "subject": {
                    "corporate": corporate.address,
                    "new_user_count": "20",
                },
                "source_refs": ["fixture://local-loop-session-001"],
                "attachments": [],
                "summary": "Local deterministic witness-loop fixture.",
                "created_at": created_at,
            }
        )
        evidence_hash = HexBytes(evidence["payload_hash"])
        tx_hash = dao.functions.proposeUserCount(20, evidence_hash).transact(
            {"from": corporate_admin}
        )
        events.append(
            tx_summary(
                w3.eth.wait_for_transaction_receipt(tx_hash),
                "proposeUserCount",
            )
        )
        proposal_id = dao.functions.activeProposalId().call()
        payload_hash = dao.functions.proposalPayloadHash(proposal_id).call()

        vote_deadline = int(w3.eth.get_block("latest")["timestamp"]) + 3600
        votes = []
        vote_summary = []
        zero_hash = "0x" + "0" * 64
        for witness in witnesses:
            nonce = dao.functions.voteNonces(witness).call()
            typed = build_vote_typed_data(
                {
                    "chain_id": str(w3.eth.chain_id),
                    "verifying_contract": dao.address,
                    "witness": witness,
                    "proposal_id": str(proposal_id),
                    "support": True,
                    "reason_hash": zero_hash,
                    "payload_hash": Web3.to_hex(payload_hash),
                    "nonce": str(nonce),
                    "deadline": str(vote_deadline),
                }
            )
            v, r, s = sign_typed_data(w3, witness, typed)
            votes.append(
                (
                    witness,
                    proposal_id,
                    True,
                    bytes(32),
                    payload_hash,
                    nonce,
                    vote_deadline,
                    v,
                    r,
                    s,
                )
            )
            vote_summary.append(
                {
                    "witness": witness,
                    "support": True,
                    "nonce": str(nonce),
                    "deadline": str(vote_deadline),
                }
            )
        tx_hash = dao.functions.batchVote(votes).transact({"from": relayer})
        events.append(
            tx_summary(
                w3.eth.wait_for_transaction_receipt(tx_hash),
                "batchVote",
            )
        )

        total_votes, support_votes = dao.functions.proposalVoteCounts(
            proposal_id
        ).call()
        manifest = read_json(DEFAULT_MANIFEST)
        transcript = {
            "schema_version": "loveengine.local-loop-transcript/1",
            "run_id": "local-loop-001",
            "manifest_hash": manifest["package_hash"],
            "chain_id": str(w3.eth.chain_id),
            "contracts": {
                "StreamingEngine": engine.address,
                "PublicSink": public_sink.address,
                "CorporateSink": corporate.address,
                "WitnessDAO": dao.address,
            },
            "events": events,
            "evidence_bundle": evidence,
            "proposal": {
                "proposal_id": str(proposal_id),
                "payload_hash": Web3.to_hex(payload_hash),
                "total_votes": str(total_votes),
                "support_votes": str(support_votes),
            },
            "votes": vote_summary,
            "final_state": {
                "registered_witness_count": str(
                    sum(
                        1
                        for witness in witnesses
                        if dao.functions.registeredWitnesses(witness).call()
                    )
                ),
                "proposal_executed": dao.functions.proposalExecuted(
                    proposal_id
                ).call(),
                "rate": str(engine.functions.rate().call()),
                "total_uto": str(public_sink.functions.getTotalUTO().call()),
            },
        }
        transcript["transcript_hash"] = transcript_hash(transcript)
        output.mkdir(parents=True, exist_ok=True)
        transcript_path = (output / "local-loop.fixture.json").resolve()
        write_json(transcript_path, transcript)
        return {
            "chain_id": str(w3.eth.chain_id),
            "registered_witness_count": "5",
            "proposal_id": str(proposal_id),
            "proposal_executed": True,
            "total_uto": transcript["final_state"]["total_uto"],
            "transcript_path": str(transcript_path),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
