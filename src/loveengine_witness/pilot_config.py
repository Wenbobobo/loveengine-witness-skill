"""Pilot configuration, invite construction, and readiness checks."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from web3 import HTTPProvider, Web3

from .errors import LoveEngineError
from .jsonio import read_json
from .network_typed_data import id_hash
from .package import verify_package
from .registry import verify_onchain_release
from .rpc_endpoints import resolve_rpc_endpoint
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


@dataclass(frozen=True)
class PilotAdminSurfaceConfig:
    host: str
    port: int
    allowed_origins: tuple[str, ...]


@dataclass(frozen=True)
class PilotParticipantSurfaceConfig:
    host: str
    port: int
    public_base_url: str


@dataclass(frozen=True)
class PilotConfigV2:
    schema_version: str
    run_id: str
    admin: PilotAdminSurfaceConfig
    participant: PilotParticipantSurfaceConfig
    database: Path
    relay_database: Path
    artifact_root: Path
    audit_log: Path
    token_file: Path
    bootstrap_file: Path
    release_file: Path
    package_archive: Path
    rpc_url: str | None = field(repr=False)
    rpc_url_file: Path | None = field(repr=False)
    chain_id: str
    write_token: str = field(repr=False)


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def read_pilot_write_token(token_file: Path) -> str:
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
    return token


def validate_admin_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        parsed.port
    except (TypeError, ValueError) as exc:
        raise LoveEngineError(
            "invalid_admin_origin",
            "admin origins must be exact loopback HTTP(S) origins",
        ) from exc
    return value


def _validate_participant_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".ts.net")
            or parsed.hostname.count(".") < 3
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        parsed.port
    except (TypeError, ValueError) as exc:
        raise LoveEngineError(
            "invalid_participant_origin",
            "participant public base URL must be an exact HTTPS origin",
        ) from exc
    return value


def _validate_participant_v2_origin(value: str, chain_id: str) -> str:
    if str(chain_id) == "11155111":
        return _validate_participant_origin(value)
    if str(chain_id) == "31337":
        return validate_admin_origin(value)
    raise LoveEngineError(
        "unsupported_pilot_chain", "Pilot V2 supports local Anvil or Sepolia"
    )


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
    token = read_pilot_write_token(token_file)
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


def load_pilot_config_v2(path: Path) -> PilotConfigV2:
    path = Path(path).resolve()
    value = read_json(path)
    chain_id = str(value.get("chain_id", "")) if isinstance(value, dict) else ""
    if chain_id == "11155111" and (
        value.get("rpc_url") or not value.get("rpc_url_file")
    ):
        raise LoveEngineError(
            "rpc_url_file_required",
            "Sepolia Pilot RPC must be loaded from a restricted file",
        )
    validate_schema(value, "pilot-config-v2.schema.json")
    admin = value["admin"]
    participant = value["participant"]
    if admin["host"] not in {"127.0.0.1", "::1"} or participant[
        "host"
    ] != "127.0.0.1":
        raise LoveEngineError(
            "pilot_v2_loopback_required",
            "both Pilot V2 listeners must bind 127.0.0.1",
        )
    if admin["port"] == participant["port"]:
        raise LoveEngineError(
            "pilot_surface_port_conflict",
            "admin and participant listeners require different ports",
        )
    allowed_origins = tuple(
        validate_admin_origin(origin) for origin in admin["allowed_origins"]
    )
    if len(set(allowed_origins)) != len(allowed_origins):
        raise LoveEngineError(
            "invalid_admin_origin", "admin origins must be unique"
        )
    public_base_url = _validate_participant_v2_origin(
        participant["public_base_url"], value["chain_id"]
    )
    token_file = _resolve_path(path.parent, value["token_file"])
    rpc_url_file = (
        _resolve_path(path.parent, value["rpc_url_file"])
        if value.get("rpc_url_file")
        else None
    )
    resolved_rpc_url = resolve_rpc_endpoint(
        direct_url=value.get("rpc_url"),
        url_file=rpc_url_file,
        chain_id=value["chain_id"],
        label="pilot",
        required=True,
    )
    assert resolved_rpc_url is not None
    return PilotConfigV2(
        schema_version=value["schema_version"],
        run_id=value["run_id"],
        admin=PilotAdminSurfaceConfig(
            host=admin["host"],
            port=admin["port"],
            allowed_origins=allowed_origins,
        ),
        participant=PilotParticipantSurfaceConfig(
            host=participant["host"],
            port=participant["port"],
            public_base_url=public_base_url,
        ),
        database=_resolve_path(path.parent, value["database"]),
        relay_database=_resolve_path(path.parent, value["relay_database"]),
        artifact_root=_resolve_path(path.parent, value["artifact_root"]),
        audit_log=_resolve_path(path.parent, value["audit_log"]),
        token_file=token_file,
        bootstrap_file=_resolve_path(path.parent, value["bootstrap_file"]),
        release_file=_resolve_path(path.parent, value["release_file"]),
        package_archive=_resolve_path(path.parent, value["package_archive"]),
        rpc_url=value.get("rpc_url"),
        rpc_url_file=rpc_url_file,
        chain_id=value["chain_id"],
        write_token=read_pilot_write_token(token_file),
    )


def load_pilot_config_any(path: Path) -> PilotConfig | PilotConfigV2:
    value = read_json(Path(path))
    schema_version = value.get("schema_version") if isinstance(value, dict) else None
    if schema_version == "loveengine.pilot-config/1":
        return load_pilot_config(path)
    if schema_version == "loveengine.pilot-config/2":
        return load_pilot_config_v2(path)
    raise LoveEngineError("unsupported_schema_version", str(schema_version))


def load_pilot_rpc_url(config: PilotConfig | PilotConfigV2) -> str:
    if isinstance(config, PilotConfigV2):
        value = resolve_rpc_endpoint(
            direct_url=config.rpc_url,
            url_file=config.rpc_url_file,
            chain_id=config.chain_id,
            label="pilot",
            required=True,
        )
        assert value is not None
        return value
    return config.rpc_url


def validate_pilot_publication(
    config: PilotConfig | PilotConfigV2,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Validate the complete Relay publication before calling it ready."""

    # Local import avoids making the configuration module part of Relay startup.
    from .relay_server import verify_relay_bootstrap

    bootstrap = read_json(config.bootstrap_file)
    verify_relay_bootstrap(bootstrap)
    release = read_json(config.release_file)
    validate_schema(release, "skill-release-v1.schema.json")
    try:
        archive = config.package_archive.read_bytes()
    except FileNotFoundError as exc:
        raise LoveEngineError(
            "package_archive_missing", str(config.package_archive), 3
        ) from exc
    package = verify_package(
        config.package_archive,
        expected_package_hash=release["package_hash"],
    )

    def require_equal(left: object, right: object, code: str, detail: str) -> None:
        if str(left).lower() != str(right).lower():
            raise LoveEngineError(code, detail)

    require_equal(
        bootstrap["chain_id"],
        config.chain_id,
        "publication_chain_mismatch",
        "bootstrap chainId does not match Pilot config",
    )
    require_equal(
        release["chain_id"],
        config.chain_id,
        "publication_chain_mismatch",
        "release chainId does not match Pilot config",
    )
    require_equal(
        release["registry"],
        bootstrap["registry"],
        "publication_registry_mismatch",
        "release Registry does not match bootstrap",
    )
    require_equal(
        release["publisher"],
        bootstrap["publisher"],
        "publication_publisher_mismatch",
        "release Publisher does not match bootstrap",
    )
    if release["skill_id"] != "loveengine-witness":
        raise LoveEngineError(
            "publication_skill_mismatch",
            "Pilot publication must contain loveengine-witness",
        )
    if release["status"] != "active":
        raise LoveEngineError(
            "publication_release_inactive", "Pilot release must be active"
        )
    require_equal(
        release["version_hash"],
        id_hash(release["version"]),
        "publication_version_mismatch",
        "release version hash does not match its version",
    )
    require_equal(
        package["skill_id"],
        release["skill_id"],
        "publication_skill_mismatch",
        "package skill does not match release",
    )
    require_equal(
        package["version"],
        release["version"],
        "publication_version_mismatch",
        "package version does not match release",
    )
    require_equal(
        package["archive_keccak256"],
        release["package_hash"],
        "package_hash_mismatch",
        "package archive does not match release",
    )
    require_equal(
        package["manifest_keccak256"],
        release["manifest_hash"],
        "publication_manifest_mismatch",
        "package manifest does not match release",
    )
    return bootstrap, release, archive


def default_pilot_readiness(
    config: PilotConfig | PilotConfigV2,
) -> tuple[bool, dict[str, Any]]:
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
    rpc_url: str | None = None
    try:
        rpc_url = load_pilot_rpc_url(config)
        web3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 1}))
        checks["chain_id"] = str(web3.eth.chain_id)
        checks["chain"] = checks["chain_id"] == config.chain_id
    except Exception:
        checks["chain"] = False
    try:
        _, release, _ = validate_pilot_publication(config)
        checks["publication"] = True
    except Exception as exc:
        checks["publication"] = False
        checks["publication_error"] = (
            exc.code if isinstance(exc, LoveEngineError) else type(exc).__name__
        )
        release = None
    try:
        if rpc_url is None or release is None:
            raise LoveEngineError(
                "registry_release_prerequisite_failed", "readiness prerequisites"
            )
        verify_onchain_release(
            config.package_archive,
            rpc_url=rpc_url,
            expected_chain_id=config.chain_id,
            registry=release["registry"],
            publisher=release["publisher"],
            skill_id=release["skill_id"],
            version=release["version"],
        )
        checks["registry_release"] = True
    except Exception as exc:
        checks["registry_release"] = False
        checks["registry_release_error"] = (
            exc.code if isinstance(exc, LoveEngineError) else type(exc).__name__
        )
    required = (
        "database",
        "relay_database",
        "artifact_root",
        "chain",
        "publication",
        "registry_release",
    )
    ready = all(checks.get(key) is True for key in required)
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


def validate_pilot_invite_v2(
    value: dict[str, Any], *, current_time: int | None = None
) -> dict[str, Any]:
    if value.get("schema_version") != "loveengine.pilot-invite/2":
        raise LoveEngineError(
            "unsupported_schema_version", str(value.get("schema_version"))
        )
    validate_schema(value, "pilot-invite-v2.schema.json")
    participant = _validate_participant_v2_origin(
        value["participant_url"], value["chain_id"]
    )
    parsed = urlsplit(participant)
    expected_dashboard = participant + "/demo/"
    relay_scheme = "wss" if parsed.scheme == "https" else "ws"
    expected_relay = urlunsplit((relay_scheme, parsed.netloc, "/v1/ws", "", ""))
    if value["dashboard_url"] != expected_dashboard:
        raise LoveEngineError(
            "invalid_pilot_invite", "dashboard URL must use the participant origin"
        )
    if value["relay_url"] != expected_relay:
        raise LoveEngineError(
            "invalid_pilot_invite", "Relay URL must use WSS on the participant origin"
        )
    if int(value["expires_at"]) <= int(value["issued_at"]):
        raise LoveEngineError(
            "invalid_pilot_invite", "expires_at must be later than issued_at"
        )
    if current_time is not None and not (
        int(value["issued_at"]) <= current_time < int(value["expires_at"])
    ):
        raise LoveEngineError(
            "pilot_invite_expired", "invite is not active at the current time"
        )
    return value


def build_pilot_invite_v2(
    *,
    participant_url: str,
    chain_id: str,
    registry: str,
    publisher: str,
    version: str,
    package_hash: str,
    issued_at: str,
    expires_at: str,
) -> dict[str, Any]:
    base = _validate_participant_v2_origin(participant_url, str(chain_id))
    parsed = urlsplit(base)
    value = {
        "schema_version": "loveengine.pilot-invite/2",
        "participant_url": base,
        "dashboard_url": base + "/demo/",
        "relay_url": urlunsplit(
            ("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/v1/ws", "", "")
        ),
        "chain_id": str(chain_id),
        "registry": registry,
        "publisher": publisher,
        "skill_id": "loveengine-witness",
        "version": version,
        "package_hash": package_hash.lower(),
        "issued_at": str(issued_at),
        "expires_at": str(expires_at),
    }
    return validate_pilot_invite_v2(value)
