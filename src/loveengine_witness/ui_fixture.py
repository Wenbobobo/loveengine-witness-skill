"""Deterministic, secret-free Pilot UI fixture for browser verification."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from aiohttp import web

from .live_evidence import finalize_evidence_bundle
from .live_gateway import ARTIFACTS_KEY, METADATA_KEY
from .live_protocol import build_live_event, build_live_session
from .pilot_server import RELAY_KEY, PilotConfig, create_pilot_app


SESSION_ID = "lan-pilot-live-001"
CONTENTS = (
    "Pilot session opened; three witness nodes are connected.",
    "The host states the purpose and the evidence boundary.",
    "Node observations agree on sequence 3 and the current head hash.",
    "A source statement is committed to content-addressed storage.",
    "The network resumes after a simulated Agent disconnect.",
    "All critical reviews return signed receipts.",
    "Session closed; evidence is finalized for ProposalGate review.",
)


def create_ui_fixture_app(root: Path) -> web.Application:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    token_file = root / "operator.token"
    token_file.write_text(
        "ui-fixture-token-not-for-production", encoding="utf-8"
    )
    config = PilotConfig(
        schema_version="loveengine.pilot-config/1",
        run_id="ui-fixture-20260623",
        host="127.0.0.1",
        port=8780,
        database=root / "pilot.sqlite",
        relay_database=root / "relay.sqlite",
        artifact_root=root / "artifacts",
        audit_log=root / "audit.jsonl",
        token_file=token_file,
        bootstrap_file=root / "bootstrap.json",
        release_file=root / "release.json",
        package_archive=root / "package.zip",
        allowed_origin="http://127.0.0.1:8780",
        rpc_url="http://127.0.0.1:1",
        chain_id="31337",
        allow_all_interfaces=False,
        write_token=token_file.read_text(encoding="utf-8"),
    )
    app = create_pilot_app(
        config,
        bootstrap={},
        readiness=lambda: (
            True,
            {
                "database": True,
                "relay_database": True,
                "artifact_root": True,
                "chain": True,
                "chain_id": "31337",
            },
        ),
    )
    metadata = app[METADATA_KEY]
    artifacts = app[ARTIFACTS_KEY]
    created_at = str(int(time.time()) - len(CONTENTS) * 4)
    metadata.create_session(
        build_live_session(SESSION_ID, "operator", created_at)
    )
    for index, content in enumerate(CONTENTS, start=1):
        session = metadata.get_session(SESSION_ID)
        artifact_hash = artifacts.put(content.encode("utf-8"))
        metadata.append_event(
            build_live_event(
                event_id=f"ui-event-{index:04d}",
                session_id=SESSION_ID,
                sequence=session["next_sequence"],
                occurred_at=str(int(created_at) + index * 4),
                category="source" if index not in {3, 6} else "summary",
                source_type="operator",
                content=content,
                artifact_hash=artifact_hash,
                previous_event_hash=session["head_event_hash"],
            )
        )
    closed_at = str(int(time.time()))
    metadata.close_session(SESSION_ID, closed_at)
    finalize_evidence_bundle(
        metadata,
        artifacts,
        SESSION_ID,
        revision="1",
        finalized_at=closed_at,
    )

    relay = app[RELAY_KEY]
    for index in range(1, 4):
        node = "0x" + f"{index:040x}"
        task_id = f"ui-observe-{index}"
        relay.connected.add(node)
        relay.store.enqueue(node, task_id, "{}", "ui-issuer", str(index))
        relay.store.pending(node)
        relay.store.accept(node, task_id)
        relay.store.ack(node, task_id, '{"status":"completed"}')
        relay.acceptance_latencies_ms.append(90.0 + index * 12)
        relay.completion_latencies_ms.append(420.0 + index * 40)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    web.run_app(
        create_ui_fixture_app(args.root),
        host=args.host,
        port=args.port,
        print=None,
    )


if __name__ == "__main__":
    main()
