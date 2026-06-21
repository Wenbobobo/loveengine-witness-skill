from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def validator(name: str) -> Draft202012Validator:
    path = SCHEMAS / name
    assert path.is_file(), f"missing schema: {path}"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_skill_manifest_v2_accepts_safe_protocol_manifest() -> None:
    value = {
        "schema_version": "loveengine.skill-manifest/0.2",
        "skill_id": "loveengine-witness",
        "version": "0.2.0-local-loop",
        "status": "draft",
        "protocol": "loveengine-witness-net/0.2",
        "source_refs": ["docs/specs/love-engine-master-plan.md"],
        "source_hashes": {
            "docs/specs/love-engine-master-plan.md": "sha256:" + "1" * 64
        },
        "commands": {},
        "contracts": {},
        "eip712": {},
        "evidence": {},
        "security": {"private_keys_in_agent_context": False},
        "package_hash": "sha256:" + "2" * 64,
    }

    validator("skill-manifest-v2.schema.json").validate(value)


def test_node_profile_rejects_private_key_access() -> None:
    value = {
        "schema_version": "loveengine.agent-node-profile/0.2",
        "node_id": "node-1",
        "agent_runtime": "codex",
        "skill_id": "loveengine-witness",
        "skill_version": "0.2.0-local-loop",
        "address": "0x" + "1" * 40,
        "signer_type": "cast",
        "capabilities": ["manifest_verification"],
        "network_endpoints": [],
        "private_key_available_to_agent": True,
        "policy": {
            "can_request_signature": True,
            "can_submit_transaction": True,
            "can_modify_source_materials": False,
        },
    }

    with pytest.raises(ValidationError):
        validator("agent-node-profile-v2.schema.json").validate(value)


def test_evidence_bundle_requires_distinct_artifact_and_payload_hashes() -> None:
    value = {
        "schema_version": "loveengine.evidence-bundle/1",
        "bundle_id": "bundle-1",
        "session_id": "session-1",
        "proposal_type": "USER_COUNT",
        "subject": {"corporate": "0x" + "2" * 40, "new_user_count": "100"},
        "source_refs": [],
        "attachments": [],
        "summary": {"kind": "summary", "text": "fixture"},
        "created_at": "2026-06-21T00:00:00Z",
        "artifact_hash": "sha256:" + "3" * 64,
        "payload_hash": "0x" + "4" * 64,
    }

    validator("evidence-bundle-v1.schema.json").validate(value)


def test_transcript_rejects_secret_fields_recursively() -> None:
    value = {
        "schema_version": "loveengine.local-loop-transcript/1",
        "run_id": "run-1",
        "manifest_hash": "sha256:" + "5" * 64,
        "chain_id": "31337",
        "contracts": {},
        "events": [],
        "evidence_bundle": {},
        "proposal": {},
        "votes": [],
        "final_state": {"private_key": "forbidden"},
        "transcript_hash": "sha256:" + "6" * 64,
    }

    with pytest.raises(ValidationError):
        validator("local-loop-transcript-v1.schema.json").validate(value)
