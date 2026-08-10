"""Separate loopback admin and participant surfaces for invited pilots."""

from __future__ import annotations

import asyncio
import hmac
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable

from aiohttp import web
from web3 import HTTPProvider, Web3

from .live_gateway import (
    ARTIFACTS_KEY,
    METADATA_KEY,
    SSE_COUNTER_KEY,
    append_events,
    close_session,
    create_session,
    dashboard_dispute,
    dashboard_index,
    dashboard_session,
    dashboard_sessions,
    error_middleware,
    finalize_evidence,
    get_artifact,
    get_events,
    get_evidence,
    get_session,
    stream_events,
)
from .live_store import LocalArtifactStore, LiveMetadataStore
from .pilot_audit import AuditLog
from .pilot_config import (
    PilotConfigV2,
    PilotParticipantSurfaceConfig,
    default_pilot_readiness,
    load_pilot_rpc_url,
)
from .pilot_task_ingress import enqueue_signed_task
from .pilot_ui import localized_operator_html
from .relay import RelayStore
from .relay_server import RelayHub


ADMIN_CONFIG_KEY = web.AppKey("pilot_admin_surface_config", object)
PARTICIPANT_CONFIG_KEY = web.AppKey("pilot_participant_surface_config", object)
SURFACE_METRICS_KEY = web.AppKey("pilot_surface_metrics", object)
SURFACE_AUDIT_KEY = web.AppKey("pilot_surface_audit", object)
SURFACE_RELAY_KEY = web.AppKey("pilot_surface_relay", object)

PARTICIPANT_ROUTE_ALLOWLIST = (
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/demo/"),
    ("GET", "/v1/bootstrap"),
    ("GET", "/v1/releases/{publisher}/{skill_id}/{version}"),
    ("GET", "/v1/artifacts/{package_hash}"),
    ("GET", "/v1/live/sessions/{session_id}"),
    ("GET", "/v1/live/sessions/{session_id}/events"),
    ("GET", "/v1/live/sessions/{session_id}/stream"),
    ("GET", "/v1/live/sessions/{session_id}/evidence"),
    ("GET", "/v1/live/artifacts/{digest}"),
    ("GET", "/v1/dashboard/sessions"),
    ("GET", "/v1/dashboard/sessions/{session_id}"),
    ("GET", "/v1/dashboard/disputes/{dispute_id}"),
    ("GET", "/v1/ws"),
)

PARTICIPANT_PATH_ALLOWLIST = (
    re.compile(r"/healthz"),
    re.compile(r"/readyz"),
    re.compile(r"/demo/"),
    re.compile(r"/v1/bootstrap"),
    re.compile(r"/v1/releases/[^/]+/[^/]+/[^/]+"),
    re.compile(r"/v1/artifacts/[^/]+"),
    re.compile(r"/v1/live/sessions/[^/]+"),
    re.compile(r"/v1/live/sessions/[^/]+/events"),
    re.compile(r"/v1/live/sessions/[^/]+/stream"),
    re.compile(r"/v1/live/sessions/[^/]+/evidence"),
    re.compile(r"/v1/live/artifacts/[^/]+"),
    re.compile(r"/v1/dashboard/sessions"),
    re.compile(r"/v1/dashboard/sessions/[^/]+"),
    re.compile(r"/v1/dashboard/disputes/[^/]+"),
    re.compile(r"/v1/ws"),
)


@dataclass
class PilotSurfaceMetrics:
    started_at: float = field(default_factory=time)
    accepted_requests: int = 0
    rejected_requests: int = 0
    sse_clients: int = 0
    recoveries: int = 0


@dataclass(frozen=True)
class PilotSurfaceApps:
    admin: web.Application
    participant: web.Application
    relay: RelayHub
    metrics: PilotSurfaceMetrics


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


def _correlation_id(request: web.Request) -> str:
    return request.headers.get("X-Correlation-ID") or str(uuid.uuid4())


def _admin_boundary(
    config: PilotConfigV2,
    metrics: PilotSurfaceMetrics,
    audit: AuditLog,
) -> Callable[[web.Request, Callable[[web.Request], Any]], Any]:
    @web.middleware
    async def boundary(
        request: web.Request, handler: Callable[[web.Request], Any]
    ) -> web.StreamResponse:
        correlation_id = _correlation_id(request)
        is_write = request.method not in {"GET", "HEAD"}
        if is_write:
            authorization = request.headers.get("Authorization", "")
            if not hmac.compare_digest(
                authorization, f"Bearer {config.write_token}"
            ):
                metrics.rejected_requests += 1
                audit.write(
                    "request",
                    surface="admin",
                    correlation_id=correlation_id,
                    method=request.method,
                    path=request.path,
                    status=401,
                )
                return web.json_response(
                    {
                        "error": {
                            "code": "write_auth_required",
                            "message": "valid Bearer token required",
                        }
                    },
                    status=401,
                    headers={"X-Correlation-ID": correlation_id},
                )
            origin = request.headers.get("Origin")
            if origin not in config.admin.allowed_origins:
                metrics.rejected_requests += 1
                audit.write(
                    "request",
                    surface="admin",
                    correlation_id=correlation_id,
                    method=request.method,
                    path=request.path,
                    status=403,
                )
                return web.json_response(
                    {"error": {"code": "origin_rejected", "message": "origin rejected"}},
                    status=403,
                    headers={"X-Correlation-ID": correlation_id},
                )
        response = await handler(request)
        metrics.accepted_requests += 1
        response.headers["X-Correlation-ID"] = correlation_id
        if is_write or response.status >= 400:
            audit.write(
                "request",
                surface="admin",
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                status=response.status,
            )
        return response

    return boundary


def _participant_boundary(
    config: PilotParticipantSurfaceConfig,
    metrics: PilotSurfaceMetrics,
    audit: AuditLog,
) -> Callable[[web.Request, Callable[[web.Request], Any]], Any]:
    @web.middleware
    async def boundary(
        request: web.Request, handler: Callable[[web.Request], Any]
    ) -> web.StreamResponse:
        correlation_id = _correlation_id(request)
        if request.method not in {"GET", "HEAD"}:
            metrics.rejected_requests += 1
            audit.write(
                "request",
                surface="participant",
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                status=405,
            )
            return web.json_response(
                {
                    "error": {
                        "code": "participant_http_write_forbidden",
                        "message": "the participant HTTP surface is read-only",
                    }
                },
                status=405,
                headers={"X-Correlation-ID": correlation_id},
            )
        if not any(pattern.fullmatch(request.path) for pattern in PARTICIPANT_PATH_ALLOWLIST):
            metrics.rejected_requests += 1
            audit.write(
                "request",
                surface="participant",
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                status=404,
            )
            return web.json_response(
                {
                    "error": {
                        "code": "participant_route_forbidden",
                        "message": "path is outside the participant allowlist",
                    }
                },
                status=404,
                headers={"X-Correlation-ID": correlation_id},
            )
        origin = request.headers.get("Origin")
        if origin and origin != config.public_base_url:
            metrics.rejected_requests += 1
            audit.write(
                "request",
                surface="participant",
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                status=403,
            )
            return web.json_response(
                {
                    "error": {
                        "code": "participant_origin_rejected",
                        "message": "participant origin rejected",
                    }
                },
                status=403,
                headers={"X-Correlation-ID": correlation_id},
            )
        response = await handler(request)
        metrics.accepted_requests += 1
        response.headers["X-Correlation-ID"] = correlation_id
        if response.status >= 400:
            audit.write(
                "request",
                surface="participant",
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                status=response.status,
            )
        return response

    return boundary


def _install_services(
    app: web.Application,
    *,
    metadata: LiveMetadataStore,
    artifacts: LocalArtifactStore,
    metrics: PilotSurfaceMetrics,
    audit: AuditLog,
    relay: RelayHub,
) -> None:
    app[METADATA_KEY] = metadata
    app[ARTIFACTS_KEY] = artifacts
    app[SSE_COUNTER_KEY] = metrics
    app[SURFACE_METRICS_KEY] = metrics
    app[SURFACE_AUDIT_KEY] = audit
    app[SURFACE_RELAY_KEY] = relay


def _read_routes() -> list[web.RouteDef]:
    return [
        web.get("/v1/live/sessions/{session_id}", get_session),
        web.get("/v1/live/sessions/{session_id}/events", get_events),
        web.get("/v1/live/sessions/{session_id}/stream", stream_events),
        web.get("/v1/live/sessions/{session_id}/evidence", get_evidence),
        web.get("/v1/live/artifacts/{digest}", get_artifact),
        web.get("/demo/", dashboard_index),
        web.get("/v1/dashboard/sessions", dashboard_sessions),
        web.get("/v1/dashboard/sessions/{session_id}", dashboard_session),
        web.get("/v1/dashboard/disputes/{dispute_id}", dashboard_dispute),
    ]


def _write_routes() -> list[web.RouteDef]:
    return [
        web.post("/v1/live/sessions", create_session),
        web.post("/v1/live/sessions/{session_id}/events", append_events),
        web.post("/v1/live/sessions/{session_id}/close", close_session),
        web.post(
            "/v1/live/sessions/{session_id}/evidence/finalize",
            finalize_evidence,
        ),
    ]


def create_pilot_surfaces(
    config: PilotConfigV2,
    *,
    bootstrap: dict[str, Any] | None = None,
    releases: dict[str, dict[str, Any]] | None = None,
    package_artifacts: dict[str, bytes] | None = None,
    readiness: Callable[[], tuple[bool, dict[str, Any]]] | None = None,
) -> PilotSurfaceApps:
    """Build two apps that share state but not HTTP authority."""

    metrics = PilotSurfaceMetrics()
    audit = AuditLog(config.audit_log, config.run_id)
    metadata = LiveMetadataStore(config.database)
    artifacts = LocalArtifactStore(config.artifact_root)
    relay = RelayHub(
        RelayStore(config.relay_database),
        bootstrap or {},
        releases or {},
        package_artifacts or {},
    )
    readiness_check = readiness or (lambda: default_pilot_readiness(config))

    def surface_name(request: web.Request) -> str:
        if ADMIN_CONFIG_KEY in request.app:
            return "admin"
        if PARTICIPANT_CONFIG_KEY in request.app:
            return "participant"
        raise LoveEngineError("pilot_surface_unknown", "surface config missing", 4)

    async def health(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "surface": surface_name(request),
                "run_id": config.run_id,
                "uptime_seconds": int(time() - metrics.started_at),
            }
        )

    async def ready(request: web.Request) -> web.Response:
        is_ready, checks = readiness_check()
        return web.json_response(
            {
                "ready": is_ready,
                "surface": surface_name(request),
                "run_id": config.run_id,
                "checks": checks,
            },
            status=200 if is_ready else 503,
        )

    async def metrics_handler(request: web.Request) -> web.Response:
        sessions = metadata.list_sessions()
        events = [
            event
            for session in sessions
            for event in metadata.list_events(session["session_id"])
        ]
        latest_event_at = max(
            (int(event["occurred_at"]) for event in events), default=int(time())
        )
        relay_metrics = relay.metrics()
        try:
            rpc_url = load_pilot_rpc_url(config)
            chain = Web3(
                HTTPProvider(rpc_url, request_kwargs={"timeout": 1})
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
        return web.json_response(
            enqueue_signed_task(relay, await request.json()), status=202
        )

    health_routes = [
        web.get("/healthz", health),
        web.get("/readyz", ready),
    ]
    admin_status_routes = [
        *health_routes,
        web.get("/v1/metrics", metrics_handler),
    ]
    relay_read_routes = []
    if bootstrap:
        relay_read_routes = [
            web.get("/v1/bootstrap", relay.get_bootstrap),
            web.get(
                "/v1/releases/{publisher}/{skill_id}/{version}",
                relay.get_release,
            ),
            web.get("/v1/artifacts/{package_hash}", relay.get_artifact),
        ]

    admin = web.Application(
        middlewares=[_admin_boundary(config, metrics, audit), error_middleware]
    )
    admin[ADMIN_CONFIG_KEY] = config
    _install_services(
        admin,
        metadata=metadata,
        artifacts=artifacts,
        metrics=metrics,
        audit=audit,
        relay=relay,
    )
    admin.add_routes(
        [
            *_read_routes(),
            *_write_routes(),
            *admin_status_routes,
            *relay_read_routes,
            web.post("/v1/relay/tasks", enqueue_relay_task),
            web.get("/operator/", operator),
        ]
    )

    participant = web.Application(
        middlewares=[
            _participant_boundary(config.participant, metrics, audit),
            error_middleware,
        ]
    )
    participant[PARTICIPANT_CONFIG_KEY] = config.participant
    _install_services(
        participant,
        metadata=metadata,
        artifacts=artifacts,
        metrics=metrics,
        audit=audit,
        relay=relay,
    )
    participant_routes = [*_read_routes(), *health_routes, *relay_read_routes]
    if bootstrap:
        participant_routes.append(web.get("/v1/ws", relay.websocket))
    participant.add_routes(participant_routes)
    return PilotSurfaceApps(
        admin=admin,
        participant=participant,
        relay=relay,
        metrics=metrics,
    )


class PilotSurfaceServer:
    """Start and stop both loopback sites as one failure-atomic unit."""

    def __init__(self, config: PilotConfigV2, apps: PilotSurfaceApps) -> None:
        self.config = config
        self.apps = apps
        self._admin_runner: web.AppRunner | None = None
        self._participant_runner: web.AppRunner | None = None

    async def start(self) -> None:
        if self._admin_runner is not None or self._participant_runner is not None:
            raise RuntimeError("Pilot surfaces are already started")
        admin = web.AppRunner(self.apps.admin)
        participant = web.AppRunner(self.apps.participant)
        try:
            await admin.setup()
            await web.TCPSite(
                admin, self.config.admin.host, self.config.admin.port
            ).start()
            self._admin_runner = admin
            await participant.setup()
            await web.TCPSite(
                participant,
                self.config.participant.host,
                self.config.participant.port,
            ).start()
            self._participant_runner = participant
        except Exception:
            self._admin_runner = None
            self._participant_runner = None
            try:
                await participant.cleanup()
            finally:
                await admin.cleanup()
            raise

    async def stop(self) -> None:
        participant = self._participant_runner
        admin = self._admin_runner
        self._participant_runner = None
        self._admin_runner = None
        try:
            if participant is not None:
                await participant.cleanup()
        finally:
            if admin is not None:
                await admin.cleanup()


async def _serve_pilot_surfaces(
    config: PilotConfigV2,
    *,
    expose_participant: bool,
    duration_seconds: int,
) -> dict[str, Any]:
    from .pilot_server import load_pilot_publication
    from .tailscale_serve import TailscaleServeManager

    bootstrap, releases, package_artifacts = load_pilot_publication(config)
    apps = create_pilot_surfaces(
        config,
        bootstrap=bootstrap,
        releases=releases,
        package_artifacts=package_artifacts,
    )
    server = PilotSurfaceServer(config, apps)
    serve_manager = (
        TailscaleServeManager(
            participant_port=config.participant.port,
            expected_public_base_url=config.participant.public_base_url,
            duration_seconds=duration_seconds,
        )
        if expose_participant
        else None
    )
    exposure: dict[str, Any] | None = None
    restored: dict[str, Any] | None = None
    await server.start()
    try:
        if serve_manager is None:
            await asyncio.Event().wait()
        exposure = serve_manager.start()
        deadline = asyncio.get_running_loop().time() + duration_seconds
        while asyncio.get_running_loop().time() < deadline:
            process = serve_manager.process
            if process is None or process.poll() is not None:
                raise LoveEngineError(
                    "tailscale_serve_process_exited",
                    "foreground Serve process exited during the bounded run",
                    4,
                )
            await asyncio.sleep(
                min(0.5, deadline - asyncio.get_running_loop().time())
            )
    finally:
        try:
            if serve_manager is not None:
                restored = serve_manager.stop()
        finally:
            await server.stop()
    return {
        "stopped": True,
        "schema_version": config.schema_version,
        "run_id": config.run_id,
        "admin_url": f"http://{config.admin.host}:{config.admin.port}",
        "participant_url": config.participant.public_base_url,
        "exposure": exposure,
        "restore": restored,
    }


def serve_pilot_surfaces(
    config: PilotConfigV2,
    *,
    expose_participant: bool = False,
    duration_seconds: int = 900,
) -> dict[str, Any]:
    """Run V2 listeners; public exposure is explicit and always time bounded."""

    if expose_participant and duration_seconds != 900:
        raise LoveEngineError(
            "invited_pilot_duration_invalid",
            "participant exposure requires the fixed 900-second acceptance window",
        )
    return asyncio.run(
        _serve_pilot_surfaces(
            config,
            expose_participant=expose_participant,
            duration_seconds=duration_seconds,
        )
    )
