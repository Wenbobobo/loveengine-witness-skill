from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.hashes import sha256_prefixed


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


def test_manifest_verify_accepts_current_m0_package() -> None:
    result = run_cli("manifest", "verify")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["valid"] is True
    assert output["version"] == "0.2.0-local-loop"


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
