from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.network_protocol import build_node_profile
from loveengine_witness.network_typed_data import build_node_profile_typed_data
from loveengine_witness.pilot_runtime import quickstart_plan
from loveengine_witness.relay_server import verify_task_for_node
from loveengine_witness.trust_policy import (
    build_node_trust_policy,
    validate_node_trust_policy,
    verify_invite_against_policy,
    verify_profile_against_policy,
    verify_release_against_policy,
)


ROOT = Path(__file__).parents[1]
CHAIN_ID = "31337"
REGISTRY = "0x" + "12" * 20
PUBLISHER = "0x" + "34" * 20
PACKAGE_HASH = "0x" + "56" * 32
MANIFEST_HASH = "0x" + "78" * 32
VERSION = "0.6.1-contract-public-pilot"


def _policy(**updates: object) -> dict:
    values = {
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "publisher": PUBLISHER,
        "skill_id": "loveengine-witness",
        "version": VERSION,
        "package_hash": PACKAGE_HASH,
        "manifest_hash": MANIFEST_HASH,
        "allowed_issuers": [PUBLISHER],
    }
    values.update(updates)
    return build_node_trust_policy(**values)


def _signed_profile(path: Path) -> tuple[dict, object]:
    account = Account.create()
    profile = build_node_profile(
        account.address,
        ["propagate_skill"],
        "1",
        "4102444800",
    )
    signature = Account.sign_message(
        encode_typed_data(
            full_message=build_node_profile_typed_data(CHAIN_ID, REGISTRY, profile)
        ),
        account.key,
    ).signature.hex()
    value = {
        "schema_version": "loveengine.signed-agent-node-profile/1",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "profile": profile,
        "signature": signature if signature.startswith("0x") else "0x" + signature,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return value, account


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loveengine_witness.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_policy_binds_invite_profile_and_release() -> None:
    policy = _policy()
    invite = {
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "publisher": PUBLISHER,
        "skill_id": "loveengine-witness",
        "version": VERSION,
        "package_hash": PACKAGE_HASH,
    }
    profile = {"chain_id": CHAIN_ID, "registry": REGISTRY}
    release = {**invite, "manifest_hash": MANIFEST_HASH}

    verify_invite_against_policy(invite, policy)
    verify_profile_against_policy(profile, policy)
    verify_release_against_policy(release, policy)

    malicious = copy.deepcopy(invite)
    malicious["publisher"] = "0x" + "90" * 20
    with pytest.raises(LoveEngineError) as error:
        verify_invite_against_policy(malicious, policy)
    assert error.value.code == "trust_policy_mismatch"


@pytest.mark.parametrize("field", ["registry", "publisher"])
def test_policy_rejects_zero_trust_addresses(field: str) -> None:
    values = _policy()
    values[field] = "0x" + "00" * 20
    with pytest.raises(LoveEngineError) as error:
        validate_node_trust_policy(values)
    assert error.value.code == "invalid_trust_policy"


def test_policy_rejects_case_insensitive_duplicate_issuers() -> None:
    policy = _policy()
    issuer = "0x" + "ab" * 20
    policy["allowed_issuers"] = [PUBLISHER, issuer, "0x" + "AB" * 20]
    with pytest.raises(LoveEngineError) as error:
        validate_node_trust_policy(policy)
    assert error.value.code == "invalid_trust_policy"


def test_policy_requires_publisher_in_allowed_issuers() -> None:
    with pytest.raises(LoveEngineError) as error:
        _policy(allowed_issuers=["0x" + "90" * 20])
    assert error.value.code == "invalid_trust_policy"


def test_task_verifier_enforces_allowed_issuers() -> None:
    fixture = json.loads(
        (ROOT / "examples/transcripts/network-pilot.fixture.json").read_text(
            encoding="utf-8"
        )
    )
    profile = fixture["bootstrap"]["directory"][0]
    task = next(
        item
        for item in fixture["tasks"]
        if item["recipient"] == profile["profile"]["node"]
    )
    verify_task_for_node(
        task,
        profile,
        expected_issuer=None,
        allowed_issuers=[task["issuer"]],
        expected_manifest_hash=task["manifest_hash"],
    )
    with pytest.raises(LoveEngineError) as error:
        verify_task_for_node(
            task,
            profile,
            expected_issuer=None,
            allowed_issuers=["0x" + "90" * 20],
            expected_manifest_hash=task["manifest_hash"],
        )
    assert error.value.code == "wrong_issuer"


def test_live_node_connect_requires_out_of_band_policy(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    _, account = _signed_profile(profile_path)
    result = _run_cli(
        "node",
        "connect",
        "--url",
        "http://127.0.0.1:1/v1/ws",
        "--profile",
        str(profile_path),
        "--package",
        str(tmp_path / "package.zip"),
        "--rpc-url",
        "http://127.0.0.1:1",
        "--address",
        account.address,
    )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"]["code"] == "trust_policy_required"


def test_node_connect_dry_run_without_policy_is_explicitly_unbound(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profile.json"
    _, _ = _signed_profile(profile_path)
    result = _run_cli(
        "node",
        "connect",
        "--url",
        "http://127.0.0.1:1/v1/ws",
        "--profile",
        str(profile_path),
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["trust_bound"] is False
    assert output["trust_policy"] is None
    assert output["verification_level"] == "connection_plan"


def test_quickstart_plan_exposes_policy_path_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "pilot"
    plan = quickstart_plan(
        root=root,
        base_url="http://127.0.0.1:8780",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
    )

    assert plan["trust_policy_path"] == str(root.resolve() / "pilot-trust-policy.json")
    assert plan["writes_state"] is False
    assert not root.exists()
