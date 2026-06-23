"""Single-process LAN Pilot Server with explicit trust boundaries."""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .hashes import keccak256_hex
from .jsonio import read_json
from .live_gateway import (
    METADATA_KEY,
    SSE_COUNTER_KEY,
    create_live_app,
)
from .relay import RelayStore
from .relay_server import RelayHub
from .schema import validate_schema


CONFIG_KEY = web.AppKey("pilot_config", object)
METRICS_KEY = web.AppKey("pilot_metrics", object)
AUDIT_KEY = web.AppKey("pilot_audit", object)
READINESS_KEY = web.AppKey("pilot_readiness", object)
RELAY_KEY = web.AppKey("pilot_relay", object)


@dataclass(frozen=True)
class PilotConfig:
    schema_version: str
    run_id: str
    host: str
    port: int
    database: Path
    relay_database: Path
    artifact_root: Path
    audit_log: Path
    token_file: Path
    bootstrap_file: Path
    release_file: Path
    package_archive: Path
    allowed_origin: str
    rpc_url: str
    chain_id: str
    allow_all_interfaces: bool
    write_token: str = field(repr=False)


@dataclass
class PilotMetrics:
    started_at: float = field(default_factory=time)
    accepted_requests: int = 0
    rejected_requests: int = 0
    sse_clients: int = 0
    recoveries: int = 0


class AuditLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.previous_hash = "sha256:" + "0" * 64
        if self.path.exists() and self.path.stat().st_size:
            result = verify_audit_log(self.path)
            self.previous_hash = result["head_hash"]

    def write(self, event: str, **details: Any) -> None:
        safe = {
            key: value
            for key, value in details.items()
            if key.lower() not in {"authorization", "token", "write_token"}
        }
        record = {
            "timestamp": int(time()),
            "run_id": self.run_id,
            "event": event,
            "previous_record_hash": self.previous_hash,
            **safe,
        }
        record["record_hash"] = sha256_prefixed(canonical_json_bytes(record))
        self.previous_hash = record["record_hash"]
        line = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        print(line, file=sys.stdout, flush=True)


def verify_audit_log(path: Path) -> dict[str, Any]:
    previous = "sha256:" + "0" * 64
    count = 0
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise LoveEngineError("audit_log_missing", str(path), 3) from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LoveEngineError(
                "audit_log_invalid", f"line {line_number}"
            ) from exc
        if record.get("previous_record_hash") != previous:
            raise LoveEngineError("audit_chain_broken", f"line {line_number}")
        expected = record.get("record_hash")
        view = dict(record)
        view.pop("record_hash", None)
        if expected != sha256_prefixed(canonical_json_bytes(view)):
            raise LoveEngineError("audit_hash_mismatch", f"line {line_number}")
        previous = expected
        count += 1
    return {"valid": True, "record_count": count, "head_hash": previous}


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_pilot_config(path: Path) -> PilotConfig:
    path = Path(path).resolve()
    value = read_json(path)
    validate_schema(value, "pilot-config-v1.schema.json")
    if value["host"] in {"0.0.0.0", "::"} and not value["allow_all_interfaces"]:
        raise LoveEngineError(
            "public_bind_not_allowed",
            "all-interface bind requires allow_all_interfaces=true",
        )
    token_file = _resolve_path(path.parent, value["token_file"])
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise LoveEngineError("pilot_token_file_missing", str(token_file), 3) from exc
    if len(token) < 16:
        raise LoveEngineError(
            "pilot_token_too_short", "write token must contain at least 16 characters"
        )
    if os.name != "nt" and token_file.stat().st_mode & 0o077:
        raise LoveEngineError(
            "pilot_token_permissions",
            "token file must not be accessible by group or other users",
        )
    return PilotConfig(
        schema_version=value["schema_version"],
        run_id=value["run_id"],
        host=value["host"],
        port=value["port"],
        database=_resolve_path(path.parent, value["database"]),
        relay_database=_resolve_path(path.parent, value["relay_database"]),
        artifact_root=_resolve_path(path.parent, value["artifact_root"]),
        audit_log=_resolve_path(path.parent, value["audit_log"]),
        token_file=token_file,
        bootstrap_file=_resolve_path(path.parent, value["bootstrap_file"]),
        release_file=_resolve_path(path.parent, value["release_file"]),
        package_archive=_resolve_path(path.parent, value["package_archive"]),
        allowed_origin=value["allowed_origin"],
        rpc_url=value["rpc_url"],
        chain_id=value["chain_id"],
        allow_all_interfaces=value["allow_all_interfaces"],
        write_token=token,
    )


def _default_readiness(config: PilotConfig) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    try:
        with sqlite3.connect(config.database) as database:
            database.execute("SELECT 1").fetchone()
        checks["database"] = True
    except sqlite3.Error:
        checks["database"] = False
    try:
        with sqlite3.connect(config.relay_database) as database:
            database.execute("SELECT 1").fetchone()
        checks["relay_database"] = True
    except sqlite3.Error:
        checks["relay_database"] = False
    config.artifact_root.mkdir(parents=True, exist_ok=True)
    checks["artifact_root"] = os.access(config.artifact_root, os.W_OK)
    web3 = Web3(HTTPProvider(config.rpc_url, request_kwargs={"timeout": 1}))
    try:
        checks["chain_id"] = str(web3.eth.chain_id)
        checks["chain"] = checks["chain_id"] == config.chain_id
    except Exception:
        checks["chain"] = False
    return all(value is True for key, value in checks.items() if key != "chain_id"), checks


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


OPERATOR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LoveEngine LAN Pilot</title>
<style>body{font-family:system-ui;max-width:900px;margin:2rem auto;padding:1rem}
input,textarea,button{display:block;width:100%;margin:.5rem 0;padding:.65rem}
pre{background:#111;color:#d6f5d6;padding:1rem;overflow:auto}</style></head>
<body><h1>LoveEngine LAN Pilot</h1>
<p>The write token is held in memory for one action and is never persisted.</p>
<input id="session" placeholder="session id"><textarea id="content" placeholder="live text"></textarea>
<button onclick="createSession()">Create session</button>
<button onclick="publishText()">Publish text</button>
<button onclick="closeSession()">Close session</button>
<h2>Live status</h2><pre id="status">Select a session.</pre><pre id="result"></pre>
<script>
const result=document.querySelector('#result');
const statusView=document.querySelector('#status');
function headers(){const token=prompt('Write token');return {'Content-Type':'application/json','Authorization':'Bearer '+token}}
async function send(path,body){const r=await fetch(path,{method:'POST',headers:headers(),body:JSON.stringify(body||{})});result.textContent=await r.text()}
function createSession(){send('/v1/live/sessions',{session_id:session.value,source_type:'operator',created_at:String(Math.floor(Date.now()/1000))})}
function publishText(){send('/v1/live/sessions/'+encodeURIComponent(session.value)+'/events',{event_id:crypto.randomUUID(),occurred_at:String(Math.floor(Date.now()/1000)),category:'source',source_type:'operator',content:content.value})}
function closeSession(){send('/v1/live/sessions/'+encodeURIComponent(session.value)+'/close',{closed_at:String(Math.floor(Date.now()/1000))})}
async function refresh(){if(!session.value)return;const [s,m]=await Promise.all([fetch('/v1/live/sessions/'+encodeURIComponent(session.value)),fetch('/v1/metrics')]);if(s.ok&&m.ok){const sv=await s.json(),mv=await m.json();statusView.textContent=JSON.stringify({sequence:Number(sv.next_sequence)-1,head_hash:sv.head_event_hash,session_status:sv.status,agents:mv.connections.agents,queued:mv.relay.queue_depth,gate:'available after dispute aggregation'},null,2)}}
setInterval(refresh,1000);
</script></body></html>"""


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

    @web.middleware
    async def pilot_boundary(
        request: web.Request, handler: Callable[[web.Request], Any]
    ) -> web.StreamResponse:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        is_write = request.method not in {"GET", "HEAD", "OPTIONS"}
        if is_write:
            origin = request.headers.get("Origin")
            if origin and origin != config.allowed_origin:
                metrics.rejected_requests += 1
                audit.write(
                    "request",
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
            authorization = request.headers.get("Authorization", "")
            expected = f"Bearer {config.write_token}"
            if not hmac.compare_digest(authorization, expected):
                metrics.rejected_requests += 1
                audit.write(
                    "request",
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
        response = await handler(request)
        metrics.accepted_requests += 1
        response.headers["X-Correlation-ID"] = correlation_id
        audit.write(
            "request",
            correlation_id=correlation_id,
            method=request.method,
            path=request.path,
            status=response.status,
        )
        return response

    app = create_live_app(config.database, config.artifact_root)
    app.middlewares.insert(0, pilot_boundary)
    app[CONFIG_KEY] = config
    app[METRICS_KEY] = metrics
    app[SSE_COUNTER_KEY] = metrics
    app[AUDIT_KEY] = audit
    app[READINESS_KEY] = readiness or (lambda: _default_readiness(config))

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
            {"status": "ok", "run_id": config.run_id, "uptime_seconds": int(time() - metrics.started_at)}
        )

    async def ready(request: web.Request) -> web.Response:
        is_ready, checks = app[READINESS_KEY]()
        return web.json_response(
            {"ready": is_ready, "run_id": config.run_id, "checks": checks},
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
        return web.Response(text=OPERATOR_HTML, content_type="text/html")

    app.add_routes(
        [
            web.get("/healthz", health),
            web.get("/readyz", ready),
            web.get("/v1/metrics", metrics_handler),
            web.get("/operator/", operator),
        ]
    )
    return app


def serve_pilot(config: PilotConfig) -> None:
    bootstrap = read_json(config.bootstrap_file)
    release = read_json(config.release_file)
    validate_schema(release, "skill-release-v1.schema.json")
    try:
        archive = config.package_archive.read_bytes()
    except FileNotFoundError as exc:
        raise LoveEngineError(
            "package_archive_missing", str(config.package_archive), 3
        ) from exc
    actual_hash = keccak256_hex(archive)
    if release["package_hash"].lower() != actual_hash.lower():
        raise LoveEngineError(
            "package_hash_mismatch", str(config.package_archive)
        )
    key = "/".join(
        (
            release["publisher"].lower(),
            release["skill_id"],
            release["version"],
        )
    )
    web.run_app(
        create_pilot_app(
            config,
            bootstrap=bootstrap,
            releases={key: release},
            package_artifacts={actual_hash.lower(): archive},
        ),
        host=config.host,
        port=config.port,
    )


async def pilot_status(url: str) -> dict[str, Any]:
    base = url.rstrip("/")
    try:
        async with ClientSession(timeout=ClientTimeout(total=3)) as session:
            values: dict[str, Any] = {}
            for name, path in (
                ("health", "/healthz"),
                ("readiness", "/readyz"),
                ("metrics", "/v1/metrics"),
            ):
                async with session.get(base + path) as response:
                    values[name] = await response.json()
                    values[name]["http_status"] = response.status
            return {"url": base, **values}
    except (ClientError, TimeoutError, OSError, ValueError) as exc:
        raise LoveEngineError("pilot_unavailable", f"{base}: {exc}", 4) from exc
