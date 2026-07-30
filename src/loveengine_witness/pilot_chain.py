"""Persistent local Anvil lifecycle for the LAN pilot."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .demo import CONTRACTS, RATE_PER_USER, deploy, wait_for_anvil
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json, write_json
from .toolchain import foundry_binary, verify_prepared_contract_artifacts


CHAIN_ID = 31337
DEPLOYMENT_FILE = "deployment.json"
STATE_FILE = "state.json"


def _rpc(w3: Web3, method: str, params: list[Any]) -> Any:
    response = w3.provider.make_request(method, params)
    if "error" in response:
        raise LoveEngineError("anvil_rpc_error", str(response["error"]), 4)
    return response["result"]


def _spawn(port: int) -> subprocess.Popen[bytes]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [
            str(foundry_binary("anvil")),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            str(CHAIN_ID),
            "--silent",
        ],
        cwd=CONTRACTS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _state_wrapper(state: str) -> dict[str, str]:
    value = {"format": "anvil_dumpState/v1", "state": state}
    value["checksum"] = sha256_prefixed(canonical_json_bytes(value))
    return value


def _verify_state(path: Path) -> dict[str, str]:
    value = read_json(path)
    expected = value.get("checksum")
    view = dict(value)
    view.pop("checksum", None)
    if expected != sha256_prefixed(canonical_json_bytes(view)):
        raise LoveEngineError("snapshot_checksum_mismatch", str(path))
    if value.get("format") != "anvil_dumpState/v1":
        raise LoveEngineError("snapshot_format_invalid", str(path))
    return value


def initialize_chain(root: Path, *, port: int) -> dict[str, Any]:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Compilation is deliberate: active Pilot paths only consume artifacts
    # produced by ``loveengine pilot contracts prepare``.
    verify_prepared_contract_artifacts(CONTRACTS)
    process = _spawn(port)
    w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        wait_for_anvil(w3, process)
        accounts = [Web3.to_checksum_address(value) for value in w3.eth.accounts]
        deployer, corporate_admin = accounts[0], accounts[1]
        engine, engine_receipt = deploy(
            w3, deployer, "StreamingEngine", RATE_PER_USER, 10
        )
        public_sink, public_receipt = deploy(
            w3, deployer, "PublicSink", engine.address
        )
        corporate, corporate_receipt = deploy(
            w3, deployer, "CorporateSink", corporate_admin, 14 * 86400, 7200
        )
        dao, dao_receipt = deploy(
            w3,
            deployer,
            "WitnessDAO",
            engine.address,
            corporate.address,
            corporate_admin,
            5,
            9000,
        )
        registry, registry_receipt = deploy(w3, deployer, "SkillRegistry")
        for contract in (engine, corporate):
            tx = contract.functions.setWitnessDAO(dao.address).transact(
                {"from": deployer}
            )
            w3.eth.wait_for_transaction_receipt(tx)
        deployed = {
            "StreamingEngine": (engine.address, engine_receipt),
            "PublicSink": (public_sink.address, public_receipt),
            "CorporateSink": (corporate.address, corporate_receipt),
            "WitnessDAO": (dao.address, dao_receipt),
            "SkillRegistry": (registry.address, registry_receipt),
        }
        contracts = {
            name: {
                "address": address,
                "code_hash": Web3.to_hex(Web3.keccak(w3.eth.get_code(address))),
                "deployment_transaction": Web3.to_hex(receipt.transactionHash),
                "deployment_block": str(receipt.blockNumber),
            }
            for name, (address, receipt) in deployed.items()
        }
        manifest = {
            "schema_version": "loveengine.pilot-chain/1",
            "chain_id": str(w3.eth.chain_id),
            "rpc_url": f"http://127.0.0.1:{port}",
            "start_block": "0",
            "deployer": deployer,
            "corporate_admin": corporate_admin,
            "witnesses": accounts[2:7],
            "relayer": accounts[7],
            "contracts": contracts,
        }
        write_json(root / DEPLOYMENT_FILE, manifest)
        write_json(root / STATE_FILE, _state_wrapper(_rpc(w3, "anvil_dumpState", [])))
        return manifest
    finally:
        process.terminate()
        process.wait(timeout=10)


def start_chain(root: Path, *, port: int) -> subprocess.Popen[bytes]:
    root = Path(root).resolve()
    if not (root / DEPLOYMENT_FILE).is_file() or not (root / STATE_FILE).is_file():
        raise LoveEngineError("chain_not_initialized", str(root), 3)
    process = _spawn(port)
    w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        wait_for_anvil(w3, process)
        state = _verify_state(root / STATE_FILE)
        if not _rpc(w3, "anvil_loadState", [state["state"]]):
            raise LoveEngineError("anvil_restore_failed", str(root), 4)
        write_json(
            root / "runtime.json",
            {
                "pid": process.pid,
                "port": port,
                "rpc_url": f"http://127.0.0.1:{port}",
                "started_at": str(int(time.time())),
            },
        )
        return process
    except Exception:
        process.terminate()
        process.wait(timeout=10)
        raise


def status_chain(root: Path, rpc_url: str) -> dict[str, Any]:
    deployment = read_json(Path(root) / DEPLOYMENT_FILE)
    w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 2}))
    if not w3.is_connected():
        raise LoveEngineError("rpc_unavailable", rpc_url, 4)
    if str(w3.eth.chain_id) != deployment["chain_id"]:
        raise LoveEngineError("wrong_chain_id", str(w3.eth.chain_id), 4)
    checks: dict[str, bool] = {}
    for name, value in deployment["contracts"].items():
        checks[name] = (
            Web3.to_hex(Web3.keccak(w3.eth.get_code(value["address"])))
            == value["code_hash"]
        )
    return {
        "valid": all(checks.values()),
        "chain_id": str(w3.eth.chain_id),
        "block_number": str(w3.eth.block_number),
        "contracts": checks,
    }


def snapshot_chain(root: Path, rpc_url: str) -> dict[str, Any]:
    root = Path(root).resolve()
    w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
    if not w3.is_connected():
        raise LoveEngineError("rpc_unavailable", rpc_url, 4)
    wrapper = _state_wrapper(_rpc(w3, "anvil_dumpState", []))
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    path = snapshots / f"state-{w3.eth.block_number}.json"
    write_json(path, wrapper)
    write_json(root / STATE_FILE, wrapper)
    return {
        "snapshot": str(path.resolve()),
        "checksum": wrapper["checksum"],
        "block_number": str(w3.eth.block_number),
    }


def restore_chain(root: Path, rpc_url: str, snapshot: Path) -> dict[str, Any]:
    value = _verify_state(Path(snapshot))
    w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
    if not w3.is_connected():
        raise LoveEngineError("rpc_unavailable", rpc_url, 4)
    restored = bool(_rpc(w3, "anvil_loadState", [value["state"]]))
    if not restored:
        raise LoveEngineError("anvil_restore_failed", str(snapshot), 4)
    write_json(Path(root) / STATE_FILE, value)
    return {
        "restored": True,
        "snapshot": str(Path(snapshot).resolve()),
        "block_number": str(w3.eth.block_number),
    }


def stop_chain(root: Path, process: subprocess.Popen[bytes]) -> None:
    runtime = Path(root) / "runtime.json"
    process.terminate()
    process.wait(timeout=10)
    runtime.unlink(missing_ok=True)
