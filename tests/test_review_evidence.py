from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.live_gateway import METADATA_KEY, create_live_app
from loveengine_witness.review_evidence import verify_review_evidence


def test_review_node_recomputes_bundle_event_chain_and_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = create_live_app(tmp_path / "live.sqlite", tmp_path / "artifacts")

        async def tampered_events(request: web.Request) -> web.Response:
            metadata = request.app[METADATA_KEY]
            events = metadata.list_events("review-session")
            events[0]["content"] = "tampered"
            return web.json_response({"events": events})

        app.router.add_get("/tampered-events", tampered_events)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            session_id = "review-session"
            assert (
                await client.post(
                    "/v1/live/sessions",
                    json={
                        "session_id": session_id,
                        "source_type": "operator",
                        "created_at": "1770000000",
                    },
                )
            ).status == 201
            assert (
                await client.post(
                    f"/v1/live/sessions/{session_id}/events",
                    json={
                        "event_id": "review-event-1",
                        "occurred_at": "1770000001",
                        "category": "source",
                        "source_type": "operator",
                        "content": "review this evidence",
                    },
                )
            ).status == 202
            assert (
                await client.post(
                    f"/v1/live/sessions/{session_id}/close",
                    json={"closed_at": "1770000002"},
                )
            ).status == 200
            finalized = await client.post(
                f"/v1/live/sessions/{session_id}/evidence/finalize",
                json={"revision": "1", "finalized_at": "1770000002"},
            )
            bundle = await finalized.json()
            base = str(client.make_url("")).rstrip("/")
            payload = {
                "schema_version": "loveengine.review-dispute-payload/1",
                "dispute_id": "dispute-1",
                "bundle_hash": bundle["bundle_hash"],
                "session_id": session_id,
                "evidence_url": (
                    f"{base}/v1/live/sessions/{session_id}/evidence"
                ),
                "events_url": (
                    f"{base}/v1/live/sessions/{session_id}/events"
                ),
                "artifact_base_url": f"{base}/v1/live/artifacts",
                "revision": bundle["revision"],
                "event_count": bundle["event_count"],
                "head_event_hash": bundle["head_event_hash"],
            }

            verified = await verify_review_evidence(
                payload,
                allowed_origin=base,
            )
            assert verified["evidence_verified"] is True
            assert verified["event_count"] == "1"

            wrong_bundle = copy.deepcopy(payload)
            wrong_bundle["bundle_hash"] = "0x" + "ff" * 32
            with pytest.raises(LoveEngineError) as error:
                await verify_review_evidence(
                    wrong_bundle,
                    allowed_origin=base,
                )
            assert error.value.code == "review_bundle_mismatch"

            tampered = copy.deepcopy(payload)
            tampered["events_url"] = f"{base}/tampered-events"
            with pytest.raises(LoveEngineError) as error:
                await verify_review_evidence(
                    tampered,
                    allowed_origin=base,
                )
            assert error.value.code == "event_hash_mismatch"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_review_urls_are_bound_to_invite_origin() -> None:
    payload = {
        "schema_version": "loveengine.review-dispute-payload/1",
        "dispute_id": "dispute-1",
        "bundle_hash": "0x" + "11" * 32,
        "session_id": "session-1",
        "evidence_url": "http://169.254.169.254/evidence",
        "events_url": "http://127.0.0.1:8780/events",
        "artifact_base_url": "http://127.0.0.1:8780/artifacts",
        "revision": "1",
        "event_count": "1",
        "head_event_hash": "0x" + "22" * 32,
    }

    with pytest.raises(LoveEngineError) as error:
        asyncio.run(
            verify_review_evidence(
                payload,
                allowed_origin="http://127.0.0.1:8780",
            )
        )
    assert error.value.code == "review_origin_mismatch"
