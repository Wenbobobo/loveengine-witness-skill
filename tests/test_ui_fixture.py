from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from loveengine_witness.ui_fixture import create_ui_fixture_app


def test_ui_fixture_exposes_stable_observable_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = TestClient(TestServer(create_ui_fixture_app(tmp_path)))
        await client.start_server()
        try:
            session = await client.get(
                "/v1/dashboard/sessions/lan-pilot-live-001"
            )
            value = await session.json()
            assert session.status == 200
            assert value["session"]["status"] == "closed"
            assert len(value["events"]) == 7
            assert value["artifact_integrity"] is True

            metrics = await client.get("/v1/metrics")
            metric_value = await metrics.json()
            assert metric_value["connections"]["agents"] == 3
            assert metric_value["relay"]["acked"] == 3
        finally:
            await client.close()

    asyncio.run(scenario())
