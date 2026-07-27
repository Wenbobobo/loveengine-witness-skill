"""Pilot HTTP write-boundary middleware."""

from __future__ import annotations

import hmac
import uuid
from typing import Any, Callable

from aiohttp import web

from .pilot_audit import AuditLog
from .pilot_config import PilotConfig


def build_pilot_boundary(
    config: PilotConfig, metrics: Any, audit: AuditLog
) -> Callable[[web.Request, Callable[[web.Request], Any]], Any]:
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

    return pilot_boundary
