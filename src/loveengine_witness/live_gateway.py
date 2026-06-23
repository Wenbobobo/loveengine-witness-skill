"""aiohttp LiveGateway and read-only demonstration dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Any

from aiohttp import web

from .errors import LoveEngineError
from .live_evidence import finalize_evidence_bundle
from .live_protocol import build_live_event, build_live_session
from .live_store import LocalArtifactStore, LiveMetadataStore


METADATA_KEY = web.AppKey("metadata", LiveMetadataStore)
ARTIFACTS_KEY = web.AppKey("artifacts", LocalArtifactStore)
SSE_COUNTER_KEY = web.AppKey("sse_counter", object)


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoveEngine Live Evidence · Evidence Console</title>
<style>
:root{color-scheme:dark;--bg:#0b0d0c;--panel:#151815;--line:#30352f;--text:#ebe8de;--muted:#989b94;
--green:#9ee493;--blue:#8ecae6;--amber:#f0c36a;--red:#ff7b72}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#111711,var(--bg) 45%);color:var(--text);
font:14px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}.shell{width:min(1180px,calc(100% - 32px));margin:30px auto 60px}
header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;border-bottom:1px solid var(--line);padding-bottom:20px}
.eyebrow{color:var(--green);font-size:11px;letter-spacing:.16em;text-transform:uppercase}h1,h2{font-family:Georgia,serif}
h1{font-size:clamp(32px,5vw,52px);line-height:1;margin:6px 0}.subtitle{color:var(--muted);margin:0}
.readonly{border:1px solid #3e5e3d;color:var(--green);padding:8px 11px;border-radius:99px}.metrics{display:grid;
grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}.metric,.panel,.session{background:linear-gradient(145deg,#1b1f1b,var(--panel));
border:1px solid var(--line)}.metric{padding:14px}.metric span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase}
.metric strong{font:600 24px/1.3 Georgia,serif}.layout{display:grid;grid-template-columns:.8fr 1.4fr;gap:12px}.panel{padding:18px}
.panel h2{margin:0 0 12px}.sessions{display:grid;gap:8px}.session{padding:14px;cursor:pointer;text-align:left;color:var(--text);font:inherit}
.session:hover,.session.selected{border-color:var(--green)}.session-top{display:flex;justify-content:space-between;gap:10px}
.badge{font-size:11px;border-radius:99px;padding:2px 7px;border:1px solid var(--line);color:var(--muted)}.badge.ok{color:var(--green);border-color:#3c5f3c}
.hash{color:var(--blue);font-size:11px;word-break:break-all;margin-top:8px}.empty{color:var(--muted);padding:32px 0;text-align:center}
.detail-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.integrity{padding:8px 10px;border-left:3px solid var(--amber);background:#282212}
.integrity.ok{border-color:var(--green);background:#142014}.integrity.bad{border-color:var(--red);background:#291614}.timeline{margin-top:18px}
.event{display:grid;grid-template-columns:50px 1fr;gap:10px;padding:11px 0;border-top:1px solid var(--line)}.seq{color:var(--green)}
.event p{margin:0;white-space:pre-wrap}.meta{color:var(--muted);font-size:11px;margin-top:4px}.error{color:var(--red)}
footer{color:var(--muted);font-size:11px;margin-top:14px;display:flex;justify-content:space-between}
@media(max-width:800px){header{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:1fr 1fr}.layout{grid-template-columns:1fr}}
@media(max-width:480px){.metrics{grid-template-columns:1fr}.shell{width:calc(100% - 18px)}}
</style>
</head>
<body>
<div class="shell">
<header><div><div class="eyebrow">LoveEngine / public read model</div><h1>Evidence console</h1>
<p class="subtitle">Read-only evidence console for session continuity, artifact integrity and observable network state.</p></div>
<div class="readonly">READ ONLY · no signing</div></header>
<section class="metrics"><div class="metric"><span>Sessions</span><strong id="metric-sessions">—</strong></div>
<div class="metric"><span>Events</span><strong id="metric-events">—</strong></div><div class="metric"><span>Agents</span><strong id="metric-agents">—</strong></div>
<div class="metric"><span>Relay ACK</span><strong id="metric-acked">—</strong></div></section>
<main class="layout"><section class="panel"><h2>Live sessions</h2><div id="sessions" class="sessions"><div class="empty">Loading sessions…</div></div></section>
<section class="panel"><div id="detail"><div class="empty">Select a session to inspect its evidence chain.</div></div></section></main>
<footer><span id="run-id">run · —</span><span id="updated">not updated</span></footer>
</div>
<script>
const byId=id=>document.getElementById(id);
const node=(tag,className,text)=>{const value=document.createElement(tag);if(className)value.className=className;if(text!==undefined)value.textContent=text;return value};
let selected='';
async function jsonOrNull(path){try{const response=await fetch(path);return response.ok?await response.json():null}catch{return null}}
function renderSessions(sessions){
  const root=byId('sessions');root.replaceChildren();
  if(!sessions.length){root.append(node('div','empty','No sessions recorded.'));return}
  sessions.forEach(session=>{
    const button=node('button','session'+(selected===session.session_id?' selected':''));
    const top=node('div','session-top');top.append(node('strong','',session.session_id));
    top.append(node('span','badge '+(session.status==='closed'?'ok':''),session.status));
    button.append(top,node('div','hash',session.head_event_hash));button.addEventListener('click',()=>loadDetail(session.session_id));
    root.append(button);
  });
}
function renderDetail(value){
  const root=byId('detail');root.replaceChildren();
  if(!value){root.append(node('div','empty error','Session detail is unavailable.'));return}
  const head=node('div','detail-head'),titles=node('div');titles.append(node('div','eyebrow','Selected session'),node('h2','',value.session.session_id));
  const integrity=value.artifact_integrity===true?'verified':value.artifact_integrity===false?'failed':'pending';
  head.append(titles,node('div','integrity '+(integrity==='verified'?'ok':integrity==='failed'?'bad':''),'Artifact integrity · '+integrity));
  root.append(head,node('div','hash',value.session.head_event_hash));
  const timeline=node('div','timeline');timeline.append(node('div','eyebrow','Committed event timeline'));
  if(!value.events.length)timeline.append(node('div','empty','No committed events.'));
  value.events.forEach(event=>{const row=node('div','event'),seq=node('div','seq','#'+event.sequence),body=node('div');
    body.append(node('p','',event.content),node('div','meta',event.category+' · '+event.source_type+' · '+event.event_hash));row.append(seq,body);timeline.append(row)});
  root.append(timeline);
}
async function loadDetail(id){selected=id;const [sessions,value]=await Promise.all([
  jsonOrNull('/v1/dashboard/sessions'),jsonOrNull('/v1/dashboard/sessions/'+encodeURIComponent(id))]);
  renderSessions(sessions?.sessions||[]);renderDetail(value)}
async function refresh(){
  const [sessionData,metrics]=await Promise.all([jsonOrNull('/v1/dashboard/sessions'),jsonOrNull('/v1/metrics')]);
  const sessions=sessionData?.sessions||[];renderSessions(sessions);byId('metric-sessions').textContent=sessions.length;
  byId('metric-events').textContent=metrics?.events?.count??'—';byId('metric-agents').textContent=metrics?.connections?.agents??'—';
  byId('metric-acked').textContent=metrics?.relay?.acked??'—';byId('run-id').textContent='run · '+(metrics?.run_id||'standalone live gateway');
  byId('updated').textContent='updated · '+new Date().toLocaleTimeString();
  if(selected)renderDetail(await jsonOrNull('/v1/dashboard/sessions/'+encodeURIComponent(selected)));
  else if(sessions.length)await loadDetail(sessions[0].session_id);
}
refresh();if(!new URLSearchParams(location.search).has('static'))setInterval(refresh,3000);
</script>
</body></html>"""


@web.middleware
async def error_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    try:
        return await handler(request)
    except LoveEngineError as exc:
        return web.json_response(
            {"error": {"code": exc.code, "message": exc.message}},
            status=409 if "conflict" in exc.code or "closed" in exc.code else 400,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return web.json_response(
            {"error": {"code": "invalid_input", "message": str(exc)}}, status=400
        )


def _services(request: web.Request) -> tuple[LiveMetadataStore, LocalArtifactStore]:
    return request.app[METADATA_KEY], request.app[ARTIFACTS_KEY]


async def create_session(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    body = await request.json()
    session = metadata.create_session(
        build_live_session(
            body["session_id"], body.get("source_type", "http_push"), body["created_at"]
        )
    )
    return web.json_response(session, status=201)


async def _input_events(request: web.Request) -> list[dict[str, Any]]:
    if request.content_type in {"application/x-ndjson", "application/ndjson"}:
        values = []
        for line in (await request.text()).splitlines():
            if line.strip():
                values.append(json.loads(line))
        return values
    body = await request.json()
    return body if isinstance(body, list) else [body]


async def append_events(request: web.Request) -> web.Response:
    metadata, artifacts = _services(request)
    session_id = request.match_info["session_id"]
    accepted = duplicates = 0
    results = []
    for raw in await _input_events(request):
        existing = metadata.get_event(raw["event_id"])
        if existing is not None and all(
            existing.get(key) == raw.get(key)
            for key in ("event_id", "occurred_at", "category", "source_type", "content")
        ):
            duplicates += 1
            results.append(existing)
            continue
        session = metadata.get_session(session_id)
        artifact_hash = artifacts.put(raw["content"].encode("utf-8"))
        event = build_live_event(
            event_id=raw["event_id"],
            session_id=session_id,
            sequence=raw.get("sequence", session["next_sequence"]),
            occurred_at=raw["occurred_at"],
            category=raw["category"],
            source_type=raw.get("source_type", session["source_type"]),
            content=raw["content"],
            artifact_hash=raw.get("artifact_hash", artifact_hash),
            previous_event_hash=raw.get(
                "previous_event_hash", session["head_event_hash"]
            ),
            source_uri=raw.get("source_uri"),
            media_url=raw.get("media_url"),
            media_hash=raw.get("media_hash"),
        )
        result = metadata.append_event(event)
        duplicates += int(result["duplicate"])
        accepted += int(not result["duplicate"])
        results.append(event)
    return web.json_response(
        {"accepted": accepted, "duplicates": duplicates, "events": results},
        status=202,
    )


async def close_session(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    body = await request.json() if request.can_read_body else {}
    return web.json_response(
        metadata.close_session(
            request.match_info["session_id"],
            str(body.get("closed_at", int(time()))),
        )
    )


async def get_session(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    return web.json_response(metadata.get_session(request.match_info["session_id"]))


async def get_events(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    after = int(request.query.get("after", "0"))
    return web.json_response(
        {"events": metadata.list_events(request.match_info["session_id"], after)}
    )


async def stream_events(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    counter = request.app.get(SSE_COUNTER_KEY)
    if counter is not None:
        counter.sse_clients += 1
    try:
        after = int(
            request.query.get("after", request.headers.get("Last-Event-ID", "0"))
        )
        events = metadata.list_events(request.match_info["session_id"], after)
        body = "".join(
            f"id: {event['sequence']}\nevent: live_event\ndata: "
            + json.dumps(event, ensure_ascii=False, sort_keys=True)
            + "\n\n"
            for event in events
        )
        return web.Response(
            text=body,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    finally:
        if counter is not None:
            counter.sse_clients -= 1


async def get_evidence(request: web.Request) -> web.Response:
    metadata, artifacts = _services(request)
    session_id = request.match_info["session_id"]
    try:
        bundle = metadata.get_bundle(session_id)
    except LoveEngineError as exc:
        if exc.code != "bundle_not_found":
            raise
        session = metadata.get_session(session_id)
        bundle = finalize_evidence_bundle(
            metadata,
            artifacts,
            session_id,
            revision="1",
            finalized_at=session["closed_at"],
        )
    return web.json_response(bundle)


async def get_artifact(request: web.Request) -> web.Response:
    _, artifacts = _services(request)
    return web.Response(
        body=artifacts.get(request.match_info["digest"]),
        content_type="application/octet-stream",
    )


async def dashboard_index(request: web.Request) -> web.Response:
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def dashboard_sessions(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    return web.json_response({"sessions": metadata.list_sessions()})


async def dashboard_session(request: web.Request) -> web.Response:
    metadata, artifacts = _services(request)
    session_id = request.match_info["session_id"]
    value: dict[str, Any] = {
        "session": metadata.get_session(session_id),
        "events": metadata.list_events(session_id),
    }
    try:
        value["evidence"] = metadata.get_bundle(session_id)
        value["artifact_integrity"] = all(
            artifacts.exists(item["artifact_hash"])
            for item in value["evidence"]["events"]
        )
    except LoveEngineError:
        value["evidence"] = None
        value["artifact_integrity"] = None
    return web.json_response(value)


async def dashboard_dispute(request: web.Request) -> web.Response:
    metadata, _ = _services(request)
    dispute_id = request.match_info["dispute_id"]
    return web.json_response(
        {
            "dispute": metadata.get_dispute(dispute_id),
            "reviews": metadata.list_reviews(dispute_id),
        }
    )


def create_live_app(database: Path, artifact_root: Path) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app[METADATA_KEY] = LiveMetadataStore(database)
    app[ARTIFACTS_KEY] = LocalArtifactStore(artifact_root)
    app.add_routes(
        [
            web.post("/v1/live/sessions", create_session),
            web.post("/v1/live/sessions/{session_id}/events", append_events),
            web.post("/v1/live/sessions/{session_id}/close", close_session),
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
    )
    return app


def serve_live(
    database: Path,
    artifact_root: Path,
    host: str = "127.0.0.1",
    port: int = 8780,
) -> None:
    web.run_app(create_live_app(database, artifact_root), host=host, port=port)
