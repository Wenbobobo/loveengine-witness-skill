"""NetworkTranscriptV1 hashing and verification."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .network_protocol import verify_bootstrap, verify_receipt, verify_task
from .schema import validate_schema
from .secrets import reject_secret_fields


def network_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def verify_network_transcript(value: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(value)
    validate_schema(value, "network-transcript-v1.schema.json")
    expected = network_transcript_hash(value)
    if value.get("transcript_hash") != expected:
        raise LoveEngineError("transcript_hash_mismatch", expected)
    bootstrap = value["bootstrap"]
    if bootstrap["publisher"] != value["release"]["publisher"]:
        raise LoveEngineError(
            "wrong_publisher",
            "bootstrap Publisher does not match release Publisher",
        )
    verify_bootstrap(
        bootstrap,
        value["chain_id"],
        value["contracts"]["SkillRegistry"],
    )
    if canonical_json_bytes(value["nodes"]) != canonical_json_bytes(
        bootstrap["directory"]
    ):
        raise LoveEngineError(
            "directory_mismatch",
            "transcript nodes must match the signed bootstrap directory",
        )
    nodes = {item["profile"]["node"] for item in bootstrap["directory"]}
    for task in value["tasks"]:
        verify_task(
            task,
            expected_chain_id=value["chain_id"],
            expected_registry=value["contracts"]["SkillRegistry"],
            expected_recipient=task["recipient"],
            now=int(task["deadline"]) - 1,
        )
    for receipt in value["receipts"]:
        signer = verify_receipt(
            receipt,
            value["chain_id"],
            value["contracts"]["SkillRegistry"],
        )
        if signer not in nodes:
            raise LoveEngineError("unknown_receipt_node", signer)
    return {
        "valid": True,
        "run_id": value["run_id"],
        "node_count": len(nodes),
        "task_count": len(value["tasks"]),
        "receipt_count": len(value["receipts"]),
        "transcript_hash": expected,
    }
