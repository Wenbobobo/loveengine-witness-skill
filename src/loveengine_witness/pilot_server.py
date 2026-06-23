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
        handle = Path(path).open("r", encoding="utf-8")
    except FileNotFoundError as exc:
        raise LoveEngineError("audit_log_missing", str(path), 3) from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoveEngine LAN Pilot</title>
<style>
:root{color-scheme:dark;--ink:#ece9df;--muted:#999b95;--line:#313530;--panel:#171a17;
--panel2:#1d211d;--bg:#0d100e;--green:#9ee493;--amber:#f0c36a;--red:#ff7b72;--blue:#8ecae6}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% 0,#182018 0,transparent 33%),var(--bg);
color:var(--ink);font:15px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
button,input,textarea{font:inherit}.shell{width:min(1240px,calc(100% - 32px));margin:28px auto 56px}
header{display:flex;justify-content:space-between;gap:32px;align-items:flex-end;padding:22px 0;border-bottom:1px solid var(--line)}
.eyebrow{color:var(--green);font-size:12px;letter-spacing:.15em;text-transform:uppercase}.title{margin:5px 0 4px;
font:600 clamp(30px,5vw,52px)/1.02 Georgia,serif}.lede{margin:0;color:var(--muted);max-width:720px}
.status-stack{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.pill{border:1px solid var(--line);border-radius:99px;
padding:7px 10px;color:var(--muted);background:#121512}.pill.ok{border-color:#365c37;color:var(--green)}
.pill.bad{border-color:#6a3835;color:var(--red)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}
.metric,.panel{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);box-shadow:0 14px 32px #0004}
.metric{padding:15px}.metric label{display:block;color:var(--muted);font-size:11px;letter-spacing:.1em;text-transform:uppercase}
.metric strong{display:block;margin-top:4px;font:600 24px/1.2 Georgia,serif}.grid{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(420px,1.4fr);gap:12px}
.panel{padding:20px}.panel h2{font:600 20px/1.2 Georgia,serif;margin:0 0 4px}.panel-note{color:var(--muted);margin:0 0 18px}
.field{display:block;margin:13px 0}.field span{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}
input,textarea{width:100%;border:1px solid #3a4039;background:#0d100e;color:var(--ink);padding:11px 12px;outline:none}
input:focus,textarea:focus{border-color:var(--green);box-shadow:0 0 0 3px #9ee49318}textarea{min-height:130px;resize:vertical}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}button{border:1px solid #4e594d;background:#232923;color:var(--ink);
padding:10px 12px;cursor:pointer;text-align:left}button:hover{border-color:var(--green);color:var(--green)}
button.primary{background:var(--green);color:#0b120b;border-color:var(--green);font-weight:700}.danger{color:var(--red)}
.notice{min-height:46px;margin-top:12px;padding:10px 12px;border-left:3px solid var(--blue);background:#111714;color:#cfd7cf;white-space:pre-wrap}
.notice.error{border-color:var(--red);color:#ffc1bd}.session-head{display:flex;justify-content:space-between;gap:12px;align-items:center}
.hash{color:var(--blue);word-break:break-all;font-size:12px}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}
.fact{border-top:1px solid var(--line);padding-top:9px}.fact dt{color:var(--muted);font-size:11px;text-transform:uppercase}.fact dd{margin:4px 0 0}
.feed{display:grid;gap:7px;max-height:330px;overflow:auto}.event{display:grid;grid-template-columns:48px 1fr;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.event-seq{color:var(--green)}.event-body{white-space:pre-wrap}.empty{color:var(--muted);padding:28px 0;text-align:center}
.gate{margin-top:16px;padding:14px;border:1px solid #6b572e;background:#2c2414}.gate strong{color:var(--amber)}
footer{display:flex;justify-content:space-between;gap:20px;margin-top:14px;color:var(--muted);font-size:12px}
@media(max-width:860px){header{align-items:flex-start;flex-direction:column}.status-stack{justify-content:flex-start}.metrics{grid-template-columns:1fr 1fr}
.grid{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}}@media(max-width:520px){.shell{width:min(100% - 18px,1240px)}
.metrics,.actions,.facts{grid-template-columns:1fr}.panel{padding:16px}}
</style>
</head>
<body>
<div class="shell">
<header>
  <div><div class="eyebrow">LoveEngine / LAN Pilot</div><h1 class="title">Witness operations</h1>
  <p class="lede">Authenticated text broadcasting with visible evidence continuity, outbound Agent observation and an explicit proposal gate.</p></div>
  <div class="status-stack"><span id="health-pill" class="pill">service · checking</span><span id="ready-pill" class="pill">chain · checking</span></div>
</header>
<section class="metrics" aria-label="Pilot metrics">
  <div class="metric"><label>Agent connections</label><strong id="metric-agents">—</strong></div>
  <div class="metric"><label>Relay queue</label><strong id="metric-queue">—</strong></div>
  <div class="metric"><label>Committed events</label><strong id="metric-events">—</strong></div>
  <div class="metric"><label>Stream lag</label><strong id="metric-lag">—</strong></div>
</section>
<main class="grid">
  <section class="panel" data-panel="control">
    <h2>Broadcast control</h2><p class="panel-note">The write token stays in this page's memory and is never persisted.</p>
    <label class="field"><span>Operator token</span><input id="write-token" type="password" autocomplete="off" placeholder="Loaded from the restricted token file"></label>
    <label class="field"><span>Session ID</span><input id="session" value="lan-pilot-live-001" autocomplete="off"></label>
    <label class="field"><span>Live text</span><textarea id="content" placeholder="Publish the next observable statement…"></textarea></label>
    <div class="actions"><button class="primary" id="create-button">Create session</button><button id="publish-button">Publish text</button>
    <button class="danger" id="close-button">Close session</button><button id="refresh-button">Refresh now</button></div>
    <div id="result" class="notice" role="status">Ready. Enter the token to enable authenticated writes.</div>
  </section>
  <section class="panel" data-panel="evidence">
    <div class="session-head"><div><h2>Evidence continuity</h2><p class="panel-note">Read-only state from the canonical metadata store.</p></div><span id="session-pill" class="pill">not loaded</span></div>
    <dl class="facts"><div class="fact"><dt>Sequence</dt><dd id="sequence">—</dd></div><div class="fact"><dt>Source</dt><dd id="source-type">—</dd></div>
    <div class="fact"><dt>Chain block</dt><dd id="chain-block">—</dd></div></dl>
    <div><div class="eyebrow">Head event hash</div><div id="head-hash" class="hash">No session selected.</div></div>
    <h2 style="margin-top:22px">Event feed</h2><div id="event-feed" class="feed"><div class="empty">No committed events.</div></div>
    <div class="gate"><strong>ProposalGate: manual hold</strong><div id="gate-copy">Evidence must be finalized and every critical dispute dismissed before a proposal plan can be produced.</div></div>
  </section>
</main>
<footer><span id="run-id">run · —</span><span id="clock">—</span></footer>
</div>
<script>
const byId=id=>document.getElementById(id);
const sessionInput=byId('session'),contentInput=byId('content'),tokenInput=byId('write-token');
let writeToken='';
tokenInput.addEventListener('input',event=>{writeToken=event.target.value});
function setPill(id,text,state){const node=byId(id);node.textContent=text;node.className='pill '+(state||'')}
function notice(message,error=false){const node=byId('result');node.textContent=message;node.className='notice'+(error?' error':'')}
async function asJson(response){const text=await response.text();try{return JSON.parse(text)}catch{return {raw:text}}}
async function send(path,body){
  if(!writeToken){notice('Write blocked: enter the operator token.',true);return}
  try{
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+writeToken},body:JSON.stringify(body||{})});
    const value=await asJson(response);
    if(!response.ok){notice((value.error&&value.error.code?value.error.code+': ':'')+(value.error?.message||'Request rejected.'),true);return}
    notice('Accepted · correlation '+(response.headers.get('X-Correlation-ID')||'not returned'));
    if(path.endsWith('/events'))contentInput.value='';
    await refresh();
  }catch(error){notice('Service unavailable: '+error.message,true)}
}
function now(){return String(Math.floor(Date.now()/1000))}
byId('create-button').addEventListener('click',()=>send('/v1/live/sessions',{session_id:sessionInput.value,source_type:'operator',created_at:now()}));
byId('publish-button').addEventListener('click',()=>send('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/events',
  {event_id:crypto.randomUUID(),occurred_at:now(),category:'source',source_type:'operator',content:contentInput.value}));
byId('close-button').addEventListener('click',()=>send('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/close',{closed_at:now()}));
byId('refresh-button').addEventListener('click',()=>refresh());
function renderEvents(events){
  const feed=byId('event-feed');feed.replaceChildren();
  if(!events.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No committed events.';feed.append(empty);return}
  events.slice(-8).reverse().forEach(event=>{
    const row=document.createElement('div');row.className='event';
    const seq=document.createElement('div');seq.className='event-seq';seq.textContent='#'+event.sequence;
    const body=document.createElement('div');body.className='event-body';body.textContent=event.content;
    row.append(seq,body);feed.append(row);
  });
}
async function refresh(){
  byId('clock').textContent=new Date().toLocaleTimeString();
  try{
    const [healthResponse,readyResponse,metricsResponse]=await Promise.all([fetch('/healthz'),fetch('/readyz'),fetch('/v1/metrics')]);
    const health=await asJson(healthResponse),ready=await asJson(readyResponse),metrics=await asJson(metricsResponse);
    setPill('health-pill','service · '+(healthResponse.ok?'online':'degraded'),healthResponse.ok?'ok':'bad');
    setPill('ready-pill','chain · '+(ready.ready?'ready':'not ready'),ready.ready?'ok':'bad');
    byId('metric-agents').textContent=metrics.connections?.agents??'—';byId('metric-queue').textContent=metrics.relay?.queue_depth??'—';
    byId('metric-events').textContent=metrics.events?.count??'—';byId('metric-lag').textContent=(metrics.events?.stream_lag_seconds??'—')+'s';
    byId('chain-block').textContent=metrics.last_chain_block??'offline';byId('run-id').textContent='run · '+(metrics.run_id||health.run_id||'—');
    if(!sessionInput.value)return;
    const [sessionResponse,eventsResponse]=await Promise.all([
      fetch('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)),
      fetch('/v1/live/sessions/'+encodeURIComponent(sessionInput.value)+'/events')
    ]);
    if(!sessionResponse.ok){setPill('session-pill','session · not found','bad');renderEvents([]);return}
    const sessionValue=await sessionResponse.json(),eventsValue=await eventsResponse.json();
    setPill('session-pill','session · '+sessionValue.status,sessionValue.status==='closed'?'ok':'');
    byId('sequence').textContent=String(Number(sessionValue.next_sequence)-1);byId('source-type').textContent=sessionValue.source_type;
    byId('head-hash').textContent=sessionValue.head_event_hash;renderEvents(eventsValue.events||[]);
  }catch(error){setPill('health-pill','service · offline','bad');setPill('ready-pill','chain · unknown','bad')}
}
refresh();if(!new URLSearchParams(location.search).has('static'))setInterval(refresh,1500);
</script>
</body></html>"""


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
        if is_write or response.status >= 400:
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
