"""Authenticated ingress for signed Pilot Relay tasks."""

from __future__ import annotations

import json
from typing import Any

from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .m4_network import verify_bootstrap_v2
from .relay_server import RelayHub, verify_task_for_node
from .schema import validate_schema


def _active_release_for_task(
    hub: RelayHub,
    task: dict[str, Any],
) -> dict[str, Any]:
    bootstrap = hub.bootstrap
    matches = []
    for release in hub.releases.values():
        validate_schema(release, "skill-release-v1.schema.json")
        if (
            release["status"] == "active"
            and release["chain_id"] == bootstrap["chain_id"]
            and to_checksum_address(release["registry"])
            == to_checksum_address(bootstrap["registry"])
            and to_checksum_address(release["publisher"])
            == to_checksum_address(bootstrap["publisher"])
            and release["manifest_hash"].lower() == task["manifest_hash"].lower()
        ):
            matches.append(release)
    if len(matches) != 1:
        raise LoveEngineError(
            "release_context_not_found",
            "task does not bind exactly one active Pilot release",
        )
    return matches[0]


def enqueue_signed_task(hub: RelayHub, task: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise LoveEngineError("invalid_task", "task must be a JSON object")
    if task.get("schema_version") != "loveengine.network-task/2":
        raise LoveEngineError(
            "unsupported_task_schema",
            "Pilot task ingress accepts only NetworkTaskV2",
        )
    bootstrap = hub.bootstrap
    if bootstrap.get("schema_version") != "loveengine.bootstrap-bundle/2":
        raise LoveEngineError(
            "unsupported_bootstrap_schema",
            "Pilot task ingress requires BootstrapBundleV2",
        )
    verify_bootstrap_v2(
        bootstrap,
        bootstrap["chain_id"],
        bootstrap["registry"],
    )
    recipient = to_checksum_address(task["recipient"])
    profiles = [
        profile
        for profile in bootstrap["directory"]
        if to_checksum_address(profile["profile"]["node"]) == recipient
    ]
    if len(profiles) != 1:
        raise LoveEngineError(
            "profile_not_in_directory",
            "task recipient is not a unique signed bootstrap member",
        )
    release = _active_release_for_task(hub, task)
    verify_task_for_node(
        task,
        profiles[0],
        expected_issuer=bootstrap["publisher"],
        allowed_issuers=[bootstrap["publisher"]],
        expected_manifest_hash=release["manifest_hash"],
    )
    expected_payload_schema = {
        "observe_live_text": "loveengine.observe-live-text-payload/1",
        "review_dispute": "loveengine.review-dispute-payload/1",
    }[task["task_type"]]
    if task["payload"].get("schema_version") != expected_payload_schema:
        raise LoveEngineError(
            "executable_task_payload_required",
            "public tasks must bind the complete executable payload schema",
        )
    serialized_task = json.dumps(
        task,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    inserted = hub.store.enqueue(
        recipient,
        task["task_id"],
        serialized_task,
        task["issuer"],
        task["nonce"],
    )
    if not inserted:
        existing = hub.store.task_state(recipient, task["task_id"])
        if (
            existing is not None
            and existing["payload"] == serialized_task
            and to_checksum_address(existing["issuer"])
            == to_checksum_address(task["issuer"])
            and str(existing["nonce"]) == str(task["nonce"])
        ):
            return {
                "queued": True,
                "idempotent_replay": True,
                "task_id": task["task_id"],
                "recipient": recipient,
            }
        raise LoveEngineError(
            "task_conflict",
            "task ID or issuer nonce is already queued for this recipient",
        )
    return {
        "queued": True,
        "task_id": task["task_id"],
        "recipient": recipient,
    }
