from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from loveengine_witness.live_gateway import create_live_app


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
