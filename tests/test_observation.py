from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness import observation as observation_module
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.live_gateway import create_live_app
from loveengine_witness.m4_network import build_receipt_v2
from loveengine_witness.m4_typed_data import build_receipt_v2_typed_data
from loveengine_witness.observation import (
    ObservationCursorStore,
    aggregate_observations,
    observation_timeout_seconds,
    observe_live_session,
    validate_observation_urls,
)


REGISTRY = "0x00000000000000000000000000000000000000aa"


def _signed_receipt(account: object, node_index: int) -> dict:
    receipt = build_receipt_v2(
        chain_id="31337",
        registry=REGISTRY,
        task_id=f"observe-{node_index}",
        node=account.address,
        status="completed",
        result={
            "schema_version": "loveengine.live-observation-receipt/1",
            "session_id": "session-1",
            "observed_from": "0",
            "last_sequence": "2",
            "event_count": "2",
            "head_event_hash": "0x" + "11" * 32,
            "bundle_hash": "0x" + "22" * 32,
            "artifact_count": "2",
            "recovered_from_cursor": False,
        },
        nonce=str(node_index),
        completed_at="1770000100",
    )
    receipt["signature"] = "0x" + Account.sign_message(
        encode_typed_data(full_message=build_receipt_v2_typed_data(receipt)),
        account.key,
    ).signature.hex()
    return receipt


def test_observation_set_requires_three_distinct_matching_receipts() -> None:
    accounts = [Account.create() for _ in range(3)]
    receipts = [_signed_receipt(account, i) for i, account in enumerate(accounts)]

    result = aggregate_observations(
        receipts,
        expected_nodes={account.address for account in accounts},
        expected_chain_id="31337",
        expected_registry=REGISTRY,
    )

    assert result["node_count"] == "3"
    assert result["head_event_hash"] == "0x" + "11" * 32
    assert result["observation_set_hash"].startswith("0x")

    receipts[2]["result"]["head_event_hash"] = "0x" + "33" * 32
    with pytest.raises(LoveEngineError):
        aggregate_observations(
            receipts,
            expected_nodes={account.address for account in accounts},
            expected_chain_id="31337",
            expected_registry=REGISTRY,
        )


def test_observer_reads_sse_validates_artifacts_and_resumes_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        app = create_live_app(tmp_path / "live.sqlite", tmp_path / "artifacts")
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            session_id = "session-1"
            created = await client.post(
                "/v1/live/sessions",
                json={
                    "session_id": session_id,
                    "source_type": "operator",
                    "created_at": "1770000000",
                },
            )
            assert created.status == 201
            for index, content in enumerate(("first", "second"), start=1):
                response = await client.post(
                    f"/v1/live/sessions/{session_id}/events",
                    json={
                        "event_id": f"event-{index}",
                        "occurred_at": str(1770000000 + index),
                        "category": "source",
                        "source_type": "operator",
                        "content": content,
                    },
                )
                assert response.status == 202
            closed = await client.post(
                f"/v1/live/sessions/{session_id}/close",
                json={"closed_at": "1770000010"},
            )
            assert closed.status == 200
            finalized = await client.post(
                f"/v1/live/sessions/{session_id}/evidence/finalize",
                json={"revision": "1", "finalized_at": "1770000011"},
            )
            assert finalized.status == 200

            base = str(client.make_url("")).rstrip("/")
            payload = {
                "schema_version": "loveengine.observe-live-text-payload/1",
                "session_id": session_id,
                "stream_url": f"{base}/v1/live/sessions/{session_id}/stream",
                "session_url": f"{base}/v1/live/sessions/{session_id}",
                "artifact_base_url": f"{base}/v1/live/artifacts",
                "start_cursor": "0",
                "initial_head_hash": "0x" + "00" * 32,
            }
            cursor = ObservationCursorStore(tmp_path / "cursor.sqlite")
            first = await observe_live_session(
                payload,
                cursor,
                "node-1",
                allowed_origin=base,
            )
            assert first["event_count"] == "2"
            assert first["artifact_count"] == "2"
            assert first["recovered_from_cursor"] is False

            second = await observe_live_session(
                payload,
                cursor,
                "node-1",
                allowed_origin=base,
            )
            assert second["event_count"] == "2"
            assert second["recovered_from_cursor"] is True
            assert second["head_event_hash"] == first["head_event_hash"]

            monkeypatch.setattr(
                observation_module,
                "MAX_OBSERVATION_ARTIFACT_BYTES",
                4,
            )
            with pytest.raises(LoveEngineError) as error:
                await observe_live_session(
                    payload,
                    cursor,
                    "node-artifact-budget",
                    allowed_origin=base,
                )
            assert error.value.code == "observation_artifact_budget_exceeded"

            monkeypatch.setattr(
                observation_module,
                "MAX_OBSERVATION_ARTIFACT_BYTES",
                64 * 1024 * 1024,
            )
            monkeypatch.setattr(
                observation_module,
                "MAX_OBSERVATION_EVENTS",
                1,
            )
            with pytest.raises(LoveEngineError) as error:
                await observe_live_session(
                    payload,
                    cursor,
                    "node-event-budget",
                    allowed_origin=base,
                )
            assert error.value.code == "observation_event_limit_exceeded"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_cursor_store_rejects_rollback(tmp_path: Path) -> None:
    store = ObservationCursorStore(tmp_path / "cursor.sqlite")
    store.save("node-1", "session-1", 2, "0x" + "11" * 32, 2)
    with pytest.raises(LoveEngineError) as exc:
        store.save("node-1", "session-1", 1, "0x" + "22" * 32, 1)
    assert exc.value.code == "cursor_rollback"


def test_observation_duration_is_signed_and_bounded() -> None:
    payload = {
        "schema_version": "loveengine.observe-live-text-payload/1",
        "session_id": "session-1",
        "stream_url": "http://127.0.0.1:8780/stream",
        "session_url": "http://127.0.0.1:8780/session",
        "artifact_base_url": "http://127.0.0.1:8780/artifacts",
        "start_cursor": "0",
        "initial_head_hash": "0x" + "00" * 32,
        "max_duration_seconds": 3780,
    }

    assert observation_timeout_seconds(payload) == 3780
    payload["max_duration_seconds"] = 14701
    with pytest.raises(LoveEngineError) as error:
        observation_timeout_seconds(payload)
    assert error.value.code == "schema_validation_failed"


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("stream_url", "http://169.254.169.254/latest/meta-data"),
        ("session_url", "http://user:password@127.0.0.1:8780/session"),
        ("artifact_base_url", "http://127.0.0.1:8781/artifacts"),
    ],
)
def test_observation_urls_are_bound_to_invite_origin(
    field: str,
    url: str,
) -> None:
    payload = {
        "schema_version": "loveengine.observe-live-text-payload/1",
        "session_id": "session-1",
        "stream_url": "http://127.0.0.1:8780/stream",
        "session_url": "http://127.0.0.1:8780/session",
        "artifact_base_url": "http://127.0.0.1:8780/artifacts",
        "start_cursor": "0",
        "initial_head_hash": "0x" + "00" * 32,
    }
    payload[field] = url

    with pytest.raises(LoveEngineError) as error:
        validate_observation_urls(
            payload,
            allowed_origin="http://127.0.0.1:8780",
        )

    assert error.value.code in {
        "invalid_observation_url",
        "observation_origin_mismatch",
    }
