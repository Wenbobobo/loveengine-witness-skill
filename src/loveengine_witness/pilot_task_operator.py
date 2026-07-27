"""Local-lab operator helper for submitting a signed review task."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from time import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web3 import HTTPProvider, Web3

from .jsonio import read_json
from .m4_network import build_task_v2
from .m4_typed_data import build_task_v2_typed_data
from .pilot_config import load_pilot_config


def _post_json(
    url: str,
    value: dict,
    *,
    token: str,
    origin: str,
) -> dict:
    request = Request(
        url,
        data=json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": origin,
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Pilot rejected write with HTTP {exc.code}: {detail}"
        ) from exc


def enqueue_review_task(
    root: Path,
    *,
    profile_index: int,
    task_id: str,
    dispute_id: str,
    evidence_base_url: str | None = None,
) -> dict:
    resolved = root.resolve()
    config = load_pilot_config(resolved / "pilot-config.json")
    release = read_json(resolved / "release.json")
    bootstrap = read_json(resolved / "bootstrap.json")
    profile = read_json(resolved / "profiles" / f"node-{profile_index}.json")
    recipient = profile["profile"]["node"]
    operator_base = config.allowed_origin.rstrip("/")
    evidence_base = (evidence_base_url or operator_base).rstrip("/")
    now = int(time())
    session_id = "operator-review-" + sha256(
        task_id.encode("utf-8")
    ).hexdigest()[:16]
    _post_json(
        operator_base + "/v1/live/sessions",
        {
            "session_id": session_id,
            "source_type": "operator_review_smoke",
            "created_at": str(now),
        },
        token=config.write_token,
        origin=config.allowed_origin,
    )
    _post_json(
        operator_base + f"/v1/live/sessions/{session_id}/events",
        {
            "event_id": session_id + ":1",
            "occurred_at": str(now),
            "category": "source",
            "source_type": "operator_review_smoke",
            "content": "signed review evidence for " + dispute_id,
        },
        token=config.write_token,
        origin=config.allowed_origin,
    )
    _post_json(
        operator_base + f"/v1/live/sessions/{session_id}/close",
        {"closed_at": str(now + 1)},
        token=config.write_token,
        origin=config.allowed_origin,
    )
    bundle = _post_json(
        operator_base + f"/v1/live/sessions/{session_id}/evidence/finalize",
        {"revision": "1", "finalized_at": str(now + 1)},
        token=config.write_token,
        origin=config.allowed_origin,
    )
    task = build_task_v2(
        chain_id=config.chain_id,
        registry=release["registry"],
        task_id=task_id,
        task_type="review_dispute",
        issuer=release["publisher"],
        recipient=recipient,
        manifest_hash=release["manifest_hash"],
        payload={
            "schema_version": "loveengine.review-dispute-payload/1",
            "dispute_id": dispute_id,
            "bundle_hash": bundle["bundle_hash"],
            "session_id": session_id,
            "evidence_url": (
                evidence_base + f"/v1/live/sessions/{session_id}/evidence"
            ),
            "events_url": (
                evidence_base + f"/v1/live/sessions/{session_id}/events"
            ),
            "artifact_base_url": evidence_base + "/v1/live/artifacts",
            "revision": bundle["revision"],
            "event_count": bundle["event_count"],
            "head_event_hash": bundle["head_event_hash"],
        },
        nonce=str(int(time() * 1000)),
        deadline=bootstrap["valid_until"],
    )
    typed_data = build_task_v2_typed_data(task)
    signer = Web3(HTTPProvider(config.rpc_url))
    signed = signer.provider.make_request(
        "eth_signTypedData_v4",
        [
            release["publisher"],
            json.dumps(typed_data, separators=(",", ":")),
        ],
    )
    if "error" in signed or not isinstance(signed.get("result"), str):
        raise RuntimeError("loopback Anvil refused the task signature")
    task["signature"] = str(signed["result"])
    queued = _post_json(
        operator_base + "/v1/relay/tasks",
        task,
        token=config.write_token,
        origin=config.allowed_origin,
    )
    return {
        "queued": queued.get("queued") is True,
        "task_id": queued.get("task_id"),
        "recipient": queued.get("recipient"),
        "task": task,
    }
