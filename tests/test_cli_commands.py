from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from jsonschema import Draft202012Validator

from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.hashes import sha256_prefixed
from loveengine_witness.network_protocol import build_node_profile
from loveengine_witness.network_typed_data import build_node_profile_typed_data


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loveengine_witness.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def load_schema(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_manifest_verify_accepts_current_network_pilot_package() -> None:
    result = run_cli("manifest", "verify")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["valid"] is True
    assert output["version"] == "0.6.1-contract-public-pilot"


def test_node_declare_generates_schema_valid_profile(tmp_path: Path) -> None:
    config = tmp_path / "node.json"
    output = tmp_path / "profile.json"
    config.write_text(
        json.dumps(
            {
                "node_id": "local-1",
                "agent_runtime": "codex",
                "address": "0x" + "1" * 40,
                "signer_type": "cast",
                "capabilities": ["manifest_verification"],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli("node", "declare", "--config", str(config), "--output", str(output))

    assert result.returncode == 0, result.stderr
    profile = json.loads(output.read_text(encoding="utf-8"))
    load_schema("agent-node-profile-v2.schema.json").validate(profile)
    assert profile["private_key_available_to_agent"] is False


def test_fixture_generate_creates_requested_nodes_without_secrets(tmp_path: Path) -> None:
    output = tmp_path / "fixtures"

    result = run_cli(
        "fixture",
        "generate",
        "--witnesses",
        "5",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    files = sorted(output.glob("witness-*.json"))
    assert len(files) == 5
    for path in files:
        profile = json.loads(path.read_text())
        assert "private_key" not in profile
        assert "mnemonic" not in profile
        assert profile["private_key_available_to_agent"] is False


def test_evidence_build_generates_stable_dual_hashes(tmp_path: Path) -> None:
    session = tmp_path / "session.json"
    output = tmp_path / "evidence.json"
    session.write_text(
        json.dumps(
            {
                "bundle_id": "bundle-1",
                "session_id": "session-1",
                "proposal_type": "USER_COUNT",
                "subject": {
                    "corporate": "0x" + "2" * 40,
                    "new_user_count": "100",
                },
                "source_refs": ["fixture://session-1"],
                "attachments": [],
                "summary": "fixture summary",
                "created_at": "2026-06-21T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "evidence",
        "build",
        "--session",
        str(session),
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    load_schema("evidence-bundle-v1.schema.json").validate(evidence)
    assert evidence["artifact_hash"].startswith("sha256:")
    assert evidence["payload_hash"].startswith("0x")


def test_transcript_verify_checks_its_canonical_hash(tmp_path: Path) -> None:
    path = tmp_path / "transcript.json"
    transcript = {
        "schema_version": "loveengine.local-loop-transcript/1",
        "run_id": "run-1",
        "manifest_hash": "sha256:" + "5" * 64,
        "chain_id": "31337",
        "contracts": {},
        "events": [],
        "evidence_bundle": {},
        "proposal": {},
        "votes": [],
        "final_state": {},
    }
    transcript["transcript_hash"] = sha256_prefixed(canonical_json_bytes(transcript))
    path.write_text(json.dumps(transcript), encoding="utf-8")

    result = run_cli("transcript", "verify", str(path))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_node_declare_rejects_secret_material_with_structured_error(
    tmp_path: Path,
) -> None:
    config = tmp_path / "unsafe-node.json"
    config.write_text(
        json.dumps(
            {
                "node_id": "unsafe",
                "agent_runtime": "codex",
                "address": "0x" + "1" * 40,
                "signer_type": "cast",
                "capabilities": [],
                "private_key": "0xdeadbeef",
            }
        ),
        encoding="utf-8",
    )

    result = run_cli("node", "declare", "--config", str(config))

    assert result.returncode == 2
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "forbidden_secret_field"


def test_eip712_vote_message_and_relayer_dry_run(tmp_path: Path) -> None:
    typed_input = tmp_path / "vote-input.json"
    typed_input.write_text(
        json.dumps(
            {
                "chain_id": "31337",
                "verifying_contract": "0x" + "1" * 40,
                "witness": "0x" + "2" * 40,
                "proposal_id": "9",
                "support": True,
                "reason_hash": "0x" + "0" * 64,
                "payload_hash": "0x" + "3" * 64,
                "nonce": "0",
                "deadline": "2000",
            }
        ),
        encoding="utf-8",
    )

    typed_result = run_cli(
        "eip712",
        "vote-message",
        "--proposal-id",
        "9",
        "--input",
        str(typed_input),
    )

    assert typed_result.returncode == 0, typed_result.stderr
    assert json.loads(typed_result.stdout)["message"]["proposalId"] == 9

    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps({"signatures": [json.loads(typed_input.read_text())]}),
        encoding="utf-8",
    )
    dry_run = run_cli(
        "relayer",
        "batch-vote",
        "--input",
        str(batch),
        "--dry-run",
    )

    assert dry_run.returncode == 0, dry_run.stderr
    output = json.loads(dry_run.stdout)
    assert output == {
        "call": "batchVote",
        "signature_count": 1,
        "submitted": False,
    }


def test_argument_errors_are_machine_readable() -> None:
    result = run_cli("node", "declare")

    assert result.returncode == 2
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "invalid_arguments"


def test_network_transcript_cli_verifies_committed_fixture() -> None:
    result = run_cli(
        "network",
        "transcript",
        "verify",
        str(ROOT / "examples" / "transcripts" / "network-pilot.fixture.json"),
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["valid"] is True
    assert output["node_count"] == 3
    assert output["receipt_count"] == 6


def test_live_cli_session_ingest_close_and_finalize(tmp_path: Path) -> None:
    database = tmp_path / "live.sqlite"
    artifacts = tmp_path / "artifacts"
    bundle = tmp_path / "bundle.json"

    created = run_cli(
        "live",
        "session",
        "create",
        "--db",
        str(database),
        "--session-id",
        "cli-live-1",
        "--created-at",
        "1770000000",
    )
    assert created.returncode == 0, created.stderr

    ingested = run_cli(
        "live",
        "ingest",
        "--db",
        str(database),
        "--artifacts",
        str(artifacts),
        "--session-id",
        "cli-live-1",
        "--input",
        str(ROOT / "examples" / "live" / "live-session.fixture.ndjson"),
    )
    assert ingested.returncode == 0, ingested.stderr
    assert json.loads(ingested.stdout)["accepted"] == 12

    closed = run_cli(
        "live",
        "close",
        "--db",
        str(database),
        "--session-id",
        "cli-live-1",
        "--closed-at",
        "1770000020",
    )
    assert closed.returncode == 0, closed.stderr

    finalized = run_cli(
        "evidence",
        "finalize",
        "--db",
        str(database),
        "--artifacts",
        str(artifacts),
        "--session-id",
        "cli-live-1",
        "--finalized-at",
        "1770000021",
        "--output",
        str(bundle),
    )
    assert finalized.returncode == 0, finalized.stderr
    value = json.loads(bundle.read_text(encoding="utf-8"))
    load_schema("evidence-bundle-v2.schema.json").validate(value)
    assert value["event_count"] == "12"


@pytest.mark.integration
def test_package_cli_build_verify_install_and_self_check(tmp_path: Path) -> None:
    release = tmp_path / "release"
    build = run_cli("package", "build", "--output", str(release))
    assert build.returncode == 0, build.stderr
    built = json.loads(build.stdout)
    archive = Path(built["archive"])
    assert archive.is_file()
    assert built["archive_keccak256"].startswith("0x")

    package_hash = built["archive_keccak256"]
    verify = run_cli(
        "package", "verify", str(archive),
        "--expected-package-hash", package_hash,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["valid"] is True

    target = tmp_path / "installed"
    install = run_cli(
        "package", "install", str(archive), "--target", str(target),
        "--expected-package-hash", package_hash,
    )
    assert install.returncode == 0, install.stderr
    check = run_cli(
        "package", "self-check", "--root", str(target),
        "--expected-package-hash", package_hash,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["version"] == "0.6.1-contract-public-pilot"


def test_pilot_cli_exposes_serve_and_status_commands(tmp_path: Path) -> None:
    missing = run_cli("pilot", "serve", "--config", str(tmp_path / "missing.json"))
    assert missing.returncode == 3
    assert json.loads(missing.stderr)["error"]["code"] == "file_not_found"

    status = run_cli("pilot", "status", "--url", "http://127.0.0.1:1")
    assert status.returncode == 4
    assert json.loads(status.stderr)["error"]["code"] == "pilot_unavailable"


def test_pilot_quickstart_dry_run_is_local_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pilot"
    result = run_cli(
        "pilot",
        "quickstart",
        "--root",
        str(root),
        "--headless",
        "--dry-run",
        "--base-url",
        "http://127.0.0.1:8780",
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["started"] is False
    assert output["operator_url"] == "http://127.0.0.1:8780/operator/"
    assert output["dashboard_url"] == "http://127.0.0.1:8780/demo/"
    assert output["headless"] is True
    assert output["writes_state"] is False
    assert not root.exists()
    assert not Path(output["token_file"]).exists()
    assert "write_token" not in json.dumps(output).lower()
    assert "private_key" not in json.dumps(output).lower()


def test_pilot_quickstart_rejects_remote_bind_even_in_dry_run(tmp_path: Path) -> None:
    result = run_cli(
        "pilot",
        "quickstart",
        "--root",
        str(tmp_path / "pilot"),
        "--host",
        "0.0.0.0",
        "--base-url",
        "http://100.64.0.10:8780",
        "--dry-run",
    )

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"]["code"] == "quickstart_local_only"


def test_node_connect_accepts_invite_file_for_relay_url(tmp_path: Path) -> None:
    account = Account.create()
    profile = build_node_profile(
        account.address,
        ["propagate_skill"],
        "1",
        "4102444800",
    )
    signed_profile = {
        "schema_version": "loveengine.signed-agent-node-profile/1",
        "chain_id": "31337",
        "registry": "0x" + "12" * 20,
        "profile": profile,
        "signature": "0x"
        + Account.sign_message(
            encode_typed_data(
                full_message=build_node_profile_typed_data(
                    "31337",
                    "0x" + "12" * 20,
                    profile,
                )
            ),
            account.key,
        ).signature.hex(),
    }
    profile_path = tmp_path / "signed-profile.json"
    profile_path.write_text(json.dumps(signed_profile), encoding="utf-8")
    invite = {
        "schema_version": "loveengine.pilot-invite/1",
        "server_url": "http://100.64.0.10:8780",
        "operator_url": "http://100.64.0.10:8780/operator/",
        "dashboard_url": "http://100.64.0.10:8780/demo/",
        "relay_url": "http://100.64.0.10:8780/v1/ws",
        "chain_id": "31337",
        "registry": "0x" + "12" * 20,
        "publisher": "0x" + "34" * 20,
        "skill_id": "loveengine-witness",
        "version": "0.6.1-contract-public-pilot",
        "package_hash": "sha256:" + "56" * 32,
    }
    invite_path = tmp_path / "pilot-invite.json"
    invite_path.write_text(json.dumps(invite), encoding="utf-8")

    result = run_cli(
        "node",
        "connect",
        "--invite",
        str(invite_path),
        "--profile",
        str(profile_path),
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["url"] == invite["relay_url"]
    assert output["invite"]["dashboard_url"] == invite["dashboard_url"]
    assert output["node"] == account.address


def test_pilot_chain_and_explicit_vote_commands_are_machine_readable(
    tmp_path: Path,
) -> None:
    status = run_cli(
        "pilot",
        "chain",
        "status",
        "--root",
        str(tmp_path),
        "--rpc-url",
        "http://127.0.0.1:1",
    )
    assert status.returncode == 3
    assert json.loads(status.stderr)["error"]["code"] == "file_not_found"

    plan = tmp_path / "proposal-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": "loveengine.onchain-proposal-plan/1",
                "chain_id": "31337",
                "witness_dao": "0x" + "11" * 20,
                "proposal_id": "1",
                "payload_hash": "0x" + "22" * 32,
                "support": True,
                "reason_hash": "0x" + "00" * 32,
                "deadline": "4102444800",
            }
        ),
        encoding="utf-8",
    )
    vote = run_cli(
        "witness",
        "vote",
        "approve",
        "--proposal-plan",
        str(plan),
        "--rpc-url",
        "http://127.0.0.1:1",
        "--address",
        "0x" + "33" * 20,
    )
    assert vote.returncode == 4
    assert json.loads(vote.stderr)["error"]["code"] == "rpc_unavailable"


def test_pilot_transcript_cli_verifies_fixture_shape(tmp_path: Path) -> None:
    from loveengine_witness.pilot_transcript import pilot_transcript_hash

    transcript = {
        "schema_version": "loveengine.pilot-transcript/1",
        "run_id": "cli-pilot",
        "version": "0.6.0-contract-public-pilot",
        "package": {"archive_keccak256": "0x" + "11" * 32},
        "chain": {},
        "session": {
            "session_id": "s1",
            "status": "closed",
            "head_event_hash": "0x" + "21" * 32,
        },
        "events": [],
        "observation_set": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "evidence_bundle": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "dispute": {"status": "dismissed"},
        "proposal_gate": {"ready": True},
        "proposal": {
            "proposal_id": "1",
            "payload_hash": "0x" + "31" * 32,
        },
        "vote_approvals": [
            {"proposal_id": "1", "payload_hash": "0x" + "31" * 32}
            for _ in range(5)
        ],
        "transactions": [],
        "final_state": {"proposal_executed": True, "total_uto": "1"},
        "metrics": {},
        "faults": {},
        "snapshots": [],
    }
    transcript["transcript_hash"] = pilot_transcript_hash(transcript)
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")

    result = run_cli("pilot", "transcript", "verify", str(path))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_pilot_snapshot_cli_has_stable_missing_file_error(tmp_path: Path) -> None:
    result = run_cli(
        "pilot", "snapshot", "verify", str(tmp_path / "missing-snapshot")
    )
    assert result.returncode == 3
    assert json.loads(result.stderr)["error"]["code"] == "snapshot_not_found"
