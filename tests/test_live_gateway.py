from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from loveengine_witness.live_gateway import (
    ARTIFACTS_KEY,
    METADATA_KEY,
    create_live_app,
    stream_events,
)


def test_sse_waits_for_new_events_instead_of_busy_polling(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_live_app(tmp_path / "live.sqlite", tmp_path / "artifacts")
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            created = await client.post(
                "/v1/live/sessions",
                json={
                    "session_id": "gateway-wait",
                    "source_type": "http_push",
                    "created_at": "1770000000",
                },
            )
            assert created.status == 201

            waiting = asyncio.create_task(
                client.get("/v1/live/sessions/gateway-wait/stream?after=0")
            )
            await asyncio.sleep(0.08)
            assert not waiting.done()

            appended = await client.post(
                "/v1/live/sessions/gateway-wait/events",
                json={
                    "event_id": "e1",
                    "occurred_at": "1770000001",
                    "category": "source",
                    "source_type": "http_push",
                    "content": "hello",
                },
            )
            assert appended.status == 202
            response = await asyncio.wait_for(waiting, timeout=1)
            assert "id: 1" in await response.text()
        finally:
            await client.close()

    asyncio.run(scenario())


def test_sse_catches_events_committed_before_a_closed_snapshot() -> None:
    class ClosingSnapshotMetadata:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_events(self, _: str, __: int) -> list[dict[str, object]]:
            self.list_calls += 1
            if self.list_calls == 1:
                return []
            return [{"sequence": "1", "event_id": "final-event"}]

        @staticmethod
        def get_session(_: str) -> dict[str, str]:
            return {"status": "closed"}

    async def scenario() -> None:
        app = web.Application()
        metadata = ClosingSnapshotMetadata()
        app[METADATA_KEY] = metadata  # type: ignore[assignment]
        app[ARTIFACTS_KEY] = object()  # type: ignore[assignment]
        app.router.add_get("/sessions/{session_id}/stream", stream_events)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/sessions/closed-race/stream?after=0")
            assert response.status == 200
            body = await response.text()
            assert "id: 1" in body
            assert '"event_id": "final-event"' in body
            assert metadata.list_calls == 2
        finally:
            await client.close()

    asyncio.run(scenario())


def test_gateway_accepts_json_ndjson_and_exposes_read_only_dashboard(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = create_live_app(tmp_path / "live.sqlite", tmp_path / "artifacts")
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                "/v1/live/sessions",
                json={
                    "session_id": "gateway-1",
                    "source_type": "http_push",
                    "created_at": "1770000000",
                },
            )
            assert response.status == 201

            line = (
                '{"event_id":"e1","occurred_at":"1770000001",'
                '"category":"source","source_type":"http_push","content":"hello"}\n'
                '{"event_id":"e2","occurred_at":"1770000002",'
                '"category":"summary","source_type":"http_push","content":"summary"}\n'
            )
            response = await client.post(
                "/v1/live/sessions/gateway-1/events",
                data=line,
                headers={"Content-Type": "application/x-ndjson"},
            )
            assert response.status == 202
            assert (await response.json())["accepted"] == 2

            forged = await client.post(
                "/v1/live/sessions/gateway-1/events",
                json={
                    "event_id": "forged",
                    "occurred_at": "1770000003",
                    "category": "source",
                    "source_type": "http_push",
                    "content": "forged",
                    "artifact_hash": "sha256:" + "0" * 64,
                },
            )
            assert forged.status == 400
            assert (await forged.json())["error"]["code"] == "artifact_hash_mismatch"

            duplicate = await client.post(
                "/v1/live/sessions/gateway-1/events",
                json={
                    "event_id": "e1",
                    "occurred_at": "1770000001",
                    "category": "source",
                    "source_type": "http_push",
                    "content": "hello",
                },
            )
            assert (await duplicate.json())["duplicates"] == 1

            stream = await client.get(
                "/v1/live/sessions/gateway-1/stream?after=1"
            )
            assert stream.status == 200
            assert "event: live_event" in await stream.text()

            close = await client.post("/v1/live/sessions/gateway-1/close")
            assert close.status == 200
            evidence = await client.get("/v1/live/sessions/gateway-1/evidence")
            assert evidence.status == 400
            assert (await evidence.json())["error"]["code"] == "bundle_not_found"
            finalized = await client.post(
                "/v1/live/sessions/gateway-1/evidence/finalize",
                json={"revision": "1", "finalized_at": "1770000010"},
            )
            assert finalized.status == 200
            evidence = await client.get("/v1/live/sessions/gateway-1/evidence")
            assert evidence.status == 200
            assert (await evidence.json())["event_count"] == "2"

            dashboard = await client.get("/v1/dashboard/sessions")
            assert dashboard.status == 200
            assert (await dashboard.json())["sessions"][0]["status"] == "closed"
            page = await client.get("/demo/")
            assert page.status == 200
            assert "LoveEngine Live Evidence" in await page.text()
        finally:
            await client.close()

    asyncio.run(scenario())
