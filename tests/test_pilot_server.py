from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from loveengine_witness.live_store import LocalArtifactStore
from loveengine_witness.pilot_server import (
    AuditLog,
    create_pilot_app,
    load_pilot_config,
    verify_audit_log,
)


TOKEN = "lan-pilot-write-token"


def _config(tmp_path: Path) -> Path:
    token = tmp_path / "operator.token"
    token.write_text(TOKEN, encoding="utf-8")
    if os.name != "nt":
        token.chmod(0o600)
    (tmp_path / "bootstrap.json").write_text("{}", encoding="utf-8")
    (tmp_path / "release.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.zip").write_bytes(b"fixture")
    config = tmp_path / "pilot.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "loveengine.pilot-config/1",
                "run_id": "pilot-test-1",
                "host": "127.0.0.1",
                "port": 8780,
                "database": str(tmp_path / "pilot.sqlite"),
                "relay_database": str(tmp_path / "relay.sqlite"),
                "artifact_root": str(tmp_path / "artifacts"),
                "audit_log": str(tmp_path / "audit.jsonl"),
                "token_file": str(token),
                "bootstrap_file": str(tmp_path / "bootstrap.json"),
                "release_file": str(tmp_path / "release.json"),
                "package_archive": str(tmp_path / "package.zip"),
                "allowed_origin": "http://127.0.0.1:8780",
                "rpc_url": "http://127.0.0.1:8545",
                "chain_id": "31337",
                "allow_all_interfaces": False,
            }
        ),
        encoding="utf-8",
    )
    return config


def test_pilot_server_protects_writes_and_exposes_read_models(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = load_pilot_config(_config(tmp_path))
        app = create_pilot_app(config, readiness=lambda: (True, {"chain_id": "31337"}))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            body = {
                "session_id": "lan-session-1",
                "source_type": "operator",
                "created_at": "1770000000",
            }
            missing = await client.post("/v1/live/sessions", json=body)
            assert missing.status == 401
            wrong = await client.post(
                "/v1/live/sessions",
                json=body,
                headers={"Authorization": "Bearer wrong"},
            )
            assert wrong.status == 401
            created = await client.post(
                "/v1/live/sessions",
                json=body,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "http://127.0.0.1:8780",
                },
            )
            assert created.status == 201
            assert created.headers["X-Correlation-ID"]

            health = await client.get("/healthz")
            assert health.status == 200
            ready = await client.get("/readyz")
            assert ready.status == 200
            assert (await ready.json())["ready"] is True
            metrics = await client.get("/v1/metrics")
            metrics_value = await metrics.json()
            assert metrics_value["run_id"] == "pilot-test-1"
            assert metrics_value["requests"]["rejected"] == 2

            operator = await client.get("/operator/")
            operator_text = await operator.text()
            assert operator.status == 200
            assert "LoveEngine LAN Pilot" in operator_text
            assert "Language" in operator_text
            assert "localStorage" not in operator_text
            assert 'type="password"' in operator_text
            assert 'id="metric-agents"' in operator_text
            assert 'id="event-feed"' in operator_text
            assert "method:'POST'" in operator_text
            operator_zh = await client.get("/operator/?lang=zh-CN")
            operator_zh_text = await operator_zh.text()
            assert "见证操作台" in operator_zh_text
            assert "语言" in operator_zh_text
            assert "localStorage" not in operator_zh_text
            dashboard = await client.get("/demo/")
            assert dashboard.status == 200
            dashboard_text = await dashboard.text()
            assert "Read-only evidence console" in dashboard_text
            assert "Language" in dashboard_text
            assert "innerHTML" not in dashboard_text
            assert "method:'POST'" not in dashboard_text
            dashboard_zh = await client.get("/demo/?lang=zh-CN")
            dashboard_zh_text = await dashboard_zh.text()
            assert "只读证据面板" in dashboard_zh_text
            assert "语言" in dashboard_zh_text
            assert "method:'POST'" not in dashboard_zh_text
        finally:
            await client.close()

    asyncio.run(scenario())
    audit_path = tmp_path / "audit.jsonl"
    audit = audit_path.read_text(encoding="utf-8")
    assert TOKEN not in audit
    assert '"event":"request"' in audit
    records = [
        json.loads(line) for line in audit.splitlines() if line.strip()
    ]
    assert all(
        record["method"] == "POST"
        for record in records
        if record["event"] == "request"
    )


def test_audit_verification_streams_records(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path, "streaming-test")
    for index in range(20):
        audit.write("state_change", sequence=index)

    def reject_read_text(*args: object, **kwargs: object) -> str:
        raise AssertionError("audit verification must not load the whole file")

    monkeypatch.setattr(Path, "read_text", reject_read_text)
    result = verify_audit_log(path)
    assert result["record_count"] == 20


def test_pilot_artifact_download_and_origin_rejection(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = load_pilot_config(_config(tmp_path))
        store = LocalArtifactStore(Path(config.artifact_root))
        digest = store.put(b"auditable text")
        app = create_pilot_app(config, readiness=lambda: (True, {}))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get(f"/v1/live/artifacts/{digest}")
            assert response.status == 200
            assert await response.read() == b"auditable text"

            rejected = await client.post(
                "/v1/live/sessions",
                json={
                    "session_id": "bad-origin",
                    "source_type": "operator",
                    "created_at": "1770000000",
                },
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "https://untrusted.example",
                },
            )
            assert rejected.status == 403
        finally:
            await client.close()

    asyncio.run(scenario())


def test_pilot_evidence_finalize_requires_auth_and_get_does_not_write(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = load_pilot_config(_config(tmp_path))
        app = create_pilot_app(config, readiness=lambda: (True, {}))
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "http://127.0.0.1:8780",
        }
        try:
            created = await client.post(
                "/v1/live/sessions",
                json={
                    "session_id": "evidence-auth",
                    "source_type": "operator",
                    "created_at": "1770000000",
                },
                headers=headers,
            )
            assert created.status == 201
            closed = await client.post(
                "/v1/live/sessions/evidence-auth/close",
                json={"closed_at": "1770000001"},
                headers=headers,
            )
            assert closed.status == 200

            missing = await client.get(
                "/v1/live/sessions/evidence-auth/evidence"
            )
            assert missing.status == 400
            assert (await missing.json())["error"]["code"] == "bundle_not_found"
            missing_again = await client.get(
                "/v1/live/sessions/evidence-auth/evidence"
            )
            assert missing_again.status == 400

            denied = await client.post(
                "/v1/live/sessions/evidence-auth/evidence/finalize"
            )
            assert denied.status == 401
            finalized = await client.post(
                "/v1/live/sessions/evidence-auth/evidence/finalize",
                json={"revision": "1", "finalized_at": "1770000002"},
                headers=headers,
            )
            assert finalized.status == 200
            fetched = await client.get(
                "/v1/live/sessions/evidence-auth/evidence"
            )
            assert fetched.status == 200
            assert (await fetched.json())["event_count"] == "0"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_pilot_config_rejects_implicit_public_bind(tmp_path: Path) -> None:
    path = _config(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["host"] = "0.0.0.0"
    path.write_text(json.dumps(value), encoding="utf-8")

    try:
        load_pilot_config(path)
    except Exception as exc:
        assert getattr(exc, "code", None) == "public_bind_not_allowed"
    else:
        raise AssertionError("implicit all-interface bind must be rejected")
