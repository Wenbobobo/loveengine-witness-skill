"""Single-process LAN Pilot Server with explicit trust boundaries."""

from __future__ import annotations

import json
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from web3 import HTTPProvider, Web3

from .errors import LoveEngineError
from .live_gateway import (
    METADATA_KEY,
    SSE_COUNTER_KEY,
    create_live_app,
)
from .relay import RelayStore
from .relay_server import RelayHub
from .pilot_audit import AuditLog, verify_audit_log
from .pilot_auth import build_pilot_boundary
from .pilot_config import (
    PilotConfig,
    PilotConfigV2,
    build_pilot_invite,
    default_pilot_readiness,
    load_pilot_config,
    validate_pilot_publication,
)
from .pilot_task_ingress import enqueue_signed_task
from .pilot_ui import localized_operator_html


CONFIG_KEY = web.AppKey("pilot_config", object)
METRICS_KEY = web.AppKey("pilot_metrics", object)
AUDIT_KEY = web.AppKey("pilot_audit", object)
READINESS_KEY = web.AppKey("pilot_readiness", object)
RELAY_KEY = web.AppKey("pilot_relay", object)


@dataclass
class PilotMetrics:
    started_at: float = field(default_factory=time)
    accepted_requests: int = 0
    rejected_requests: int = 0
    sse_clients: int = 0
    recoveries: int = 0


def _disk_bytes(*roots: Path) -> int:
    total = 0
    for root in roots:
        if root.is_file():
            total += root.stat().st_size
        elif root.exists():
            total += sum(
                path.stat().st_size
                for path in root.rglob("*")
                if path.is_file()
            )
    return total


def quickstart_pilot(
    *,
    root: Path,
    base_url: str,
    host: str,
    port: int,
    rpc_port: int,
    headless: bool,
    dry_run: bool,
) -> dict[str, Any]:
    from .pilot_runtime import prepare_local_pilot_runtime, quickstart_plan

    if dry_run:
        return {
            **quickstart_plan(
                root=root,
                base_url=base_url,
                host=host,
                port=port,
                rpc_port=rpc_port,
            ),
            "headless": headless,
        }
    runtime = prepare_local_pilot_runtime(
        root=root,
        base_url=base_url,
        host=host,
        port=port,
        rpc_port=rpc_port,
    )
    info = {**runtime.info, "headless": headless}
    if not headless:
        webbrowser.open(info["operator_url"])
        webbrowser.open(info["dashboard_url"])
    print(json.dumps(info, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        web.run_app(
            create_pilot_app(
                runtime.config,
                bootstrap=runtime.bootstrap,
                releases={runtime.release_key: runtime.release},
                package_artifacts={
                    runtime.package.keccak256.lower(): runtime.package.archive.read_bytes()
                },
            ),
            host=runtime.config.host,
            port=runtime.config.port,
        )
    finally:
        runtime.close()
    return {**info, "started": False, "stopped": True}


def create_pilot_app(
    config: PilotConfig,
    *,
    bootstrap: dict[str, Any] | None = None,
    releases: dict[str, dict[str, Any]] | None = None,
    package_artifacts: dict[str, bytes] | None = None,
    readiness: Callable[[], tuple[bool, dict[str, Any]]] | None = None,
) -> web.Application:
    metrics = PilotMetrics()
    audit = AuditLog(config.audit_log, config.run_id)

    app = create_live_app(config.database, config.artifact_root)
    app.middlewares.insert(0, build_pilot_boundary(config, metrics, audit))
    app[CONFIG_KEY] = config
    app[METRICS_KEY] = metrics
    app[SSE_COUNTER_KEY] = metrics
    app[AUDIT_KEY] = audit
    app[READINESS_KEY] = readiness or (lambda: default_pilot_readiness(config))

    relay = RelayHub(
        RelayStore(config.relay_database),
        bootstrap or {},
        releases or {},
        package_artifacts or {},
    )
    app[RELAY_KEY] = relay
    if bootstrap:
        app.add_routes(
            [
                web.get("/v1/bootstrap", relay.get_bootstrap),
                web.get(
                    "/v1/releases/{publisher}/{skill_id}/{version}",
                    relay.get_release,
                ),
                web.get("/v1/artifacts/{package_hash}", relay.get_artifact),
                web.get("/v1/ws", relay.websocket),
            ]
        )

    async def health(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "surface": "combined",
                "run_id": config.run_id,
                "uptime_seconds": int(time() - metrics.started_at),
            }
        )

    async def ready(request: web.Request) -> web.Response:
        is_ready, checks = app[READINESS_KEY]()
        return web.json_response(
            {
                "ready": is_ready,
                "surface": "combined",
                "run_id": config.run_id,
                "checks": checks,
            },
            status=200 if is_ready else 503,
        )

    async def metrics_handler(request: web.Request) -> web.Response:
        relay_metrics = relay.metrics()
        sessions = app[METADATA_KEY].list_sessions()
        events = [
            event
            for session in sessions
            for event in app[METADATA_KEY].list_events(session["session_id"])
        ]
        latest_event_at = max(
            (int(event["occurred_at"]) for event in events), default=int(time())
        )
        try:
            chain = Web3(
                HTTPProvider(config.rpc_url, request_kwargs={"timeout": 1})
            )
            last_chain_block: str | None = str(chain.eth.block_number)
        except Exception:
            last_chain_block = None
        return web.json_response(
            {
                "run_id": config.run_id,
                "requests": {
                    "accepted": metrics.accepted_requests,
                    "rejected": metrics.rejected_requests,
                },
                "connections": {
                    "agents": relay_metrics["connected"],
                    "sse": metrics.sse_clients,
                },
                "relay": relay_metrics,
                "recoveries": metrics.recoveries,
                "events": {
                    "count": len(events),
                    "rate_per_second": round(
                        len(events) / max(time() - metrics.started_at, 0.001), 3
                    ),
                    "stream_lag_seconds": max(0, int(time()) - latest_event_at),
                },
                "last_chain_block": last_chain_block,
                "disk_bytes": _disk_bytes(
                    config.database,
                    config.relay_database,
                    config.artifact_root,
                    config.audit_log,
                ),
            }
        )

    async def operator(request: web.Request) -> web.Response:
        return web.Response(
            text=localized_operator_html(request.query.get("lang")),
            content_type="text/html",
        )

    async def enqueue_relay_task(request: web.Request) -> web.Response:
        result = enqueue_signed_task(relay, await request.json())
        return web.json_response(result, status=202)

    app.add_routes(
        [
            web.get("/healthz", health),
            web.get("/readyz", ready),
            web.get("/v1/metrics", metrics_handler),
            web.post("/v1/relay/tasks", enqueue_relay_task),
            web.get("/operator/", operator),
        ]
    )
    return app


def load_pilot_publication(
    config: PilotConfig | PilotConfigV2,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]]:
    bootstrap, release, archive = validate_pilot_publication(config)
    actual_hash = release["package_hash"]
    key = "/".join(
        (
            release["publisher"].lower(),
            release["skill_id"],
            release["version"],
        )
    )
    return bootstrap, {key: release}, {actual_hash.lower(): archive}


def serve_pilot(config: PilotConfig) -> None:
    bootstrap, releases, package_artifacts = load_pilot_publication(config)
    web.run_app(
        create_pilot_app(
            config,
            bootstrap=bootstrap,
            releases=releases,
            package_artifacts=package_artifacts,
        ),
        host=config.host,
        port=config.port,
    )


async def pilot_status(
    url: str,
    *,
    expected_surface: str | None = None,
    include_metrics: bool = True,
) -> dict[str, Any]:
    base = url.rstrip("/")
    try:
        async with ClientSession(timeout=ClientTimeout(total=3)) as session:
            values: dict[str, Any] = {}
            endpoints = [
                ("health", "/healthz"),
                ("readiness", "/readyz"),
            ]
            if include_metrics:
                endpoints.append(("metrics", "/v1/metrics"))
            for name, path in endpoints:
                async with session.get(base + path) as response:
                    value = await response.json()
                    value["http_status"] = response.status
                    values[name] = value
                    if response.status != 200:
                        code = (
                            "pilot_not_ready"
                            if name == "readiness"
                            else "pilot_status_failed"
                        )
                        raise LoveEngineError(
                            code, f"{base}{path}: HTTP {response.status}", 4
                        )
            if values["readiness"].get("ready") is not True:
                raise LoveEngineError(
                    "pilot_not_ready", f"{base}/readyz: ready is not true", 4
                )
            if expected_surface is not None:
                reported = {
                    values["health"].get("surface"),
                    values["readiness"].get("surface"),
                }
                if reported != {expected_surface}:
                    raise LoveEngineError(
                        "pilot_surface_mismatch",
                        f"expected {expected_surface}; reported {sorted(str(item) for item in reported)}",
                        4,
                    )
            return {"url": base, **values}
    except (ClientError, TimeoutError, OSError, ValueError) as exc:
        raise LoveEngineError("pilot_unavailable", f"{base}: {exc}", 4) from exc
