"""Shared local runtime preparation for quickstart and LAN pilot flows."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlparse

from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .demo import artifact, tx_summary
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .m4_network import build_bootstrap_v2, build_node_profile_v2
from .m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
)
from .manifest import DEFAULT_MANIFEST
from .package import PackageBuildResult, build_package
from .pilot_chain import initialize_chain, start_chain, stop_chain
from .pilot_config import PilotConfig, build_pilot_invite
from .release_identity import SKILL_VERSION
from .trust_policy import build_node_trust_policy


ROOT = Path(__file__).resolve().parents[2]
ZERO_HASH = "0x" + "00" * 32
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_local_quickstart(host: str, port: int, base_url: str) -> str:
    if host not in LOCAL_HOSTS:
        raise LoveEngineError(
            "quickstart_local_only",
            "quickstart host must be 127.0.0.1, localhost, or ::1",
        )
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise LoveEngineError(
            "quickstart_local_only",
            "quickstart base URL must use HTTP on a loopback host",
        )
    effective_port = parsed.port or 80
    if effective_port != port or parsed.path not in {"", "/"}:
        raise LoveEngineError(
            "quickstart_url_mismatch",
            "base URL must match the quickstart server port and have no path",
        )
    return base_url.rstrip("/")


def quickstart_plan(
    *, root: Path, base_url: str, host: str, port: int, rpc_port: int
) -> dict[str, Any]:
    base = validate_local_quickstart(host, port, base_url)
    resolved = Path(root).resolve()
    return {
        "mode": "local_contract_public_pilot",
        "scope": "loopback_only",
        "root": str(resolved),
        "config": str(resolved / "pilot-config.json"),
        "invite_path": str(resolved / "pilot-invite.json"),
        "trust_policy_path": str(resolved / "pilot-trust-policy.json"),
        "token_file": str(resolved / "operator.token"),
        "operator_url": base + "/operator/",
        "dashboard_url": base + "/demo/",
        "relay_url": base + "/v1/ws",
        "rpc_url": f"http://127.0.0.1:{rpc_port}",
        "steps": [
            "build deterministic package",
            "initialize or restore persistent Anvil",
            "verify or publish the local Registry release",
            "create signed node profiles and bootstrap directory",
            "start the authenticated Pilot Server and Relay",
        ],
        "writes_state": False,
        "started": False,
    }


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
        address=deployment["contracts"][name]["address"], abi=artifact(name)["abi"]
    )


def _restricted_token(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 16:
        raise LoveEngineError("pilot_token_too_short", str(path))
    return token


@dataclass
class PilotRuntime:
    config: PilotConfig
    package: PackageBuildResult
    release: dict[str, Any]
    trust_policy: dict[str, Any]
    bootstrap: dict[str, Any]
    invite: dict[str, Any]
    info: dict[str, Any]
    chain_process: Any

    @property
    def release_key(self) -> str:
        return "/".join(
            (
                self.release["publisher"].lower(),
                self.release["skill_id"],
                self.release["version"],
            )
        )

    def close(self) -> None:
        if self.chain_process.poll() is None:
            stop_chain(self.config.bootstrap_file.parent / "chain", self.chain_process)


def prepare_local_pilot_runtime(
    *,
    root: Path,
    base_url: str,
    host: str,
    port: int,
    rpc_port: int,
    run_id: str | None = None,
) -> PilotRuntime:
    """Build and anchor a complete loopback pilot before exposing the server."""

    base = validate_local_quickstart(host, port, base_url)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    package = build_package(ROOT, root / "release")
    chain_root = root / "chain"
    if not (chain_root / "deployment.json").is_file():
        initialize_chain(chain_root, port=rpc_port)
    process = start_chain(chain_root, port=rpc_port)
    try:
        rpc_url = f"http://127.0.0.1:{rpc_port}"
        w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
        deployment = read_json(chain_root / "deployment.json")
        chain_id = str(w3.eth.chain_id)
        deployer = Web3.to_checksum_address(deployment["deployer"])
        registry = _contract(w3, deployment, "SkillRegistry")
        manifest = read_json(DEFAULT_MANIFEST)
        manifest_hash = Web3.keccak(canonical_json_bytes(manifest))
        skill_hash = Web3.keccak(text="loveengine-witness")
        version_hash = Web3.keccak(text=SKILL_VERSION)
        existing = registry.functions.getRelease(
            deployer, skill_hash, version_hash
        ).call()
        transactions: list[dict[str, Any]] = []
        if int(existing[4]) == 0:
            tx = registry.functions.publishRelease(
                skill_hash,
                version_hash,
                HexBytes(package.keccak256),
                manifest_hash,
                bytes(32),
            ).transact({"from": deployer})
            transactions.append(
                tx_summary(w3.eth.wait_for_transaction_receipt(tx), "publishRelease")
            )
        elif (
            Web3.to_hex(existing[0]).lower() != package.keccak256.lower()
            or Web3.to_hex(existing[1]).lower() != Web3.to_hex(manifest_hash).lower()
            or int(existing[4]) != 1
        ):
            raise LoveEngineError(
                "quickstart_release_conflict",
                "the existing local release does not match the current package",
            )
        if Web3.to_hex(registry.functions.currentVersion(deployer, skill_hash).call()).lower() != Web3.to_hex(version_hash).lower():
            tx = registry.functions.setCurrentVersion(skill_hash, version_hash).transact(
                {"from": deployer}
            )
            transactions.append(
                tx_summary(w3.eth.wait_for_transaction_receipt(tx), "setCurrentVersion")
            )

        deadline = "4102444700"
        profile_dir = root / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profiles = []
        for index, raw_node in enumerate(w3.eth.accounts[2:5], start=1):
            node = Web3.to_checksum_address(raw_node)
            profile = build_node_profile_v2(
                node,
                ["observe_live_text", "review_dispute"],
                str(index),
                deadline,
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
        release = {
            "schema_version": "loveengine.skill-release/1",
            "chain_id": chain_id,
            "registry": registry.address,
            "publisher": deployer,
            "skill_id": "loveengine-witness",
            "version": SKILL_VERSION,
            "version_hash": Web3.to_hex(version_hash),
            "package_hash": package.keccak256,
            "manifest_hash": Web3.to_hex(manifest_hash),
            "previous_version_hash": ZERO_HASH,
            "status": "active",
        }

        token_file = root / "operator.token"
        token = _restricted_token(token_file)
        bootstrap_file = root / "bootstrap.json"
        release_file = root / "release.json"
        invite_path = root / "pilot-invite.json"
        trust_policy_path = root / "pilot-trust-policy.json"
        config_path = root / "pilot-config.json"
        write_json(bootstrap_file, bootstrap)
        write_json(release_file, release)
        invite = build_pilot_invite(
            base_url=base,
            chain_id=chain_id,
            registry=registry.address,
            publisher=deployer,
            version=SKILL_VERSION,
            package_hash=package.keccak256,
        )
        write_json(invite_path, invite)
        trust_policy = build_node_trust_policy(
            chain_id=chain_id,
            registry=registry.address,
            publisher=deployer,
            skill_id="loveengine-witness",
            version=SKILL_VERSION,
            package_hash=package.keccak256,
            manifest_hash=Web3.to_hex(manifest_hash),
            allowed_issuers=[deployer],
        )
        write_json(trust_policy_path, trust_policy)
        resolved_run_id = run_id or f"quickstart-{int(time())}"
        config_value = {
            "schema_version": "loveengine.pilot-config/1",
            "run_id": resolved_run_id,
            "host": host,
            "port": port,
            "database": str(root / "pilot.sqlite"),
            "relay_database": str(root / "relay.sqlite"),
            "artifact_root": str(root / "artifacts"),
            "audit_log": str(root / "audit.jsonl"),
            "token_file": str(token_file),
            "bootstrap_file": str(bootstrap_file),
            "release_file": str(release_file),
            "package_archive": str(package.archive),
            "allowed_origin": base,
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "allow_all_interfaces": False,
        }
        write_json(config_path, config_value)
        config = PilotConfig(
            schema_version=config_value["schema_version"],
            run_id=config_value["run_id"],
            host=host,
            port=port,
            database=root / "pilot.sqlite",
            relay_database=root / "relay.sqlite",
            artifact_root=root / "artifacts",
            audit_log=root / "audit.jsonl",
            token_file=token_file,
            bootstrap_file=bootstrap_file,
            release_file=release_file,
            package_archive=package.archive,
            allowed_origin=base,
            rpc_url=rpc_url,
            chain_id=chain_id,
            allow_all_interfaces=False,
            write_token=token,
        )
        info = {
            **quickstart_plan(
                root=root,
                base_url=base,
                host=host,
                port=port,
                rpc_port=rpc_port,
            ),
            "writes_state": True,
            "started": True,
            "chain_id": chain_id,
            "registry": registry.address,
            "publisher": deployer,
            "package_hash": package.keccak256,
            "manifest_hash": Web3.to_hex(manifest_hash),
            "profiles": [str(path.resolve()) for path in sorted(profile_dir.glob("*.json"))],
            "release_transactions": transactions,
        }
        return PilotRuntime(
            config=config,
            package=package,
            release=release,
            trust_policy=trust_policy,
            bootstrap=bootstrap,
            invite=invite,
            info=info,
            chain_process=process,
        )
    except Exception:
        if process.poll() is None:
            stop_chain(chain_root, process)
        raise
