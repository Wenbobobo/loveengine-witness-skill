"""Pilot configuration, invite construction, and readiness checks."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from web3 import HTTPProvider, Web3

from .errors import LoveEngineError
from .jsonio import read_json
from .schema import validate_schema


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


def default_pilot_readiness(config: PilotConfig) -> tuple[bool, dict[str, Any]]:
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
    ready = all(value is True for key, value in checks.items() if key != "chain_id")
    return ready, checks


def build_pilot_invite(
    *,
    base_url: str,
    chain_id: str,
    registry: str,
    publisher: str,
    version: str,
    package_hash: str,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "schema_version": "loveengine.pilot-invite/1",
        "server_url": base,
        "operator_url": base + "/operator/",
        "dashboard_url": base + "/demo/",
        "relay_url": base + "/v1/ws",
        "chain_id": chain_id,
        "registry": registry,
        "publisher": publisher,
        "skill_id": "loveengine-witness",
        "version": version,
        "package_hash": package_hash,
    }
