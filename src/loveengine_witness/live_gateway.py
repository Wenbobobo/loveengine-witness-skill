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


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LoveEngine Live Evidence</title>
<style>
body{font-family:system-ui;background:#0d1117;color:#e6edf3;max-width:960px;margin:3rem auto;padding:0 1rem}
article{border:1px solid #30363d;border-radius:12px;padding:1rem;margin:.75rem 0}
.ok{color:#3fb950}.bad{color:#f85149}code{color:#79c0ff}
</style></head><body><h1>LoveEngine Live Evidence</h1>
<p>Read-only M4 session, evidence, dispute and ProposalGate model.</p>
<main id="sessions"></main>
<script>
fetch('/v1/dashboard/sessions').then(r=>r.json()).then(x=>{
 document.querySelector('#sessions').innerHTML=x.sessions.map(s=>
 `<article><strong>${s.session_id}</strong> <span class="${s.status==='closed'?'ok':'bad'}">${s.status}</span>
 <div>head <code>${s.head_event_hash}</code></div></article>`).join('')
})
</script></body></html>"""


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
    after = int(request.query.get("after", request.headers.get("Last-Event-ID", "0")))
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
