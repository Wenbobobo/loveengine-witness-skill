"""Local-lab operator helper for submitting a signed review task."""

from __future__ import annotations

import json
from pathlib import Path
from time import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web3 import HTTPProvider, Web3

from .jsonio import read_json
from .m4_network import build_task_v2
from .m4_typed_data import build_task_v2_typed_data
from .pilot_config import load_pilot_config


def enqueue_review_task(
    root: Path,
    *,
    profile_index: int,
    task_id: str,
    dispute_id: str,
) -> dict:
    resolved = root.resolve()
    config = load_pilot_config(resolved / "pilot-config.json")
    release = read_json(resolved / "release.json")
    bootstrap = read_json(resolved / "bootstrap.json")
    profile = read_json(resolved / "profiles" / f"node-{profile_index}.json")
    recipient = profile["profile"]["node"]
    task = build_task_v2(
        chain_id=config.chain_id,
        registry=release["registry"],
        task_id=task_id,
        task_type="review_dispute",
        issuer=release["publisher"],
        recipient=recipient,
        manifest_hash=release["manifest_hash"],
        payload={
            "dispute_id": dispute_id,
            "bundle_hash": "0x" + "12" * 32,
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
    body = json.dumps(
        task,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    request = Request(
        config.allowed_origin.rstrip("/") + "/v1/relay/tasks",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.write_token}",
            "Content-Type": "application/json",
            "Origin": config.allowed_origin,
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            queued = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Pilot rejected task with HTTP {exc.code}: {detail}") from exc
    return {
        "queued": queued.get("queued") is True,
        "task_id": queued.get("task_id"),
        "recipient": queued.get("recipient"),
    }
