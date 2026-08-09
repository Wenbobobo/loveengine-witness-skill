from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loveengine_witness.errors import LoveEngineError


ROOT = Path(__file__).resolve().parents[1]
ADDRESS_A = "0x" + "11" * 20
ADDRESS_B = "0x" + "22" * 20
BYTES32_A = "0x" + "aa" * 32
BYTES32_B = "0x" + "bb" * 32


def _schema(name: str) -> dict[str, Any]:
    path = ROOT / "schemas" / name
    assert path.is_file(), f"required public schema is missing: {name}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"required public module is missing: {name}: {exc}")


def _public_symbols(module: Any, names: set[str]) -> None:
    missing = sorted(name for name in names if not hasattr(module, name))
    assert not missing, f"missing public API in {module.__name__}: {missing}"


def _route_pairs(app: Any) -> set[tuple[str, str]]:
    return {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
    }


def test_external_signer_public_contract_is_present() -> None:
    schema = _schema("external-signer-config-v1.schema.json")
    assert schema["properties"]["schema_version"]["const"] == (
        "loveengine.external-signer-config/1"
    )
    assert schema.get("additionalProperties") is False
    required = set(schema["required"])
    assert {
        "schema_version",
        "kind",
        "role",
        "address",
        "chain_id",
        "approval_mode",
        "request_timeout_seconds",
        "transport",
        "endpoint",
        "ruleset_sha256",
        "rules_attestation_sha256",
        "allowed_typed_data",
    }.issubset(required)
    forbidden_names = {
        "private_key",
        "mnemonic",
        "password",
        "credential",
        "auth_token",
        "write_token",
    }
    assert forbidden_names.isdisjoint(schema["properties"])

    signer = _module("loveengine_witness.signer_client")
    _public_symbols(
        signer,
        {
            "SignerClient",
            "ExternalSignerConfig",
            "AnvilRpcSigner",
            "load_external_signer_config",
            "parse_external_signer_config",
            "build_signer_client",
            "verify_clef_runtime_evidence",
        },
    )
    assert not hasattr(signer, "ClefSigner")
    assert not hasattr(signer, "build_verified_signer_client")
    assert {"address", "chain_id", "role"}.issubset(
        signer.SignerClient.__annotations__
    )
    for method in ("sign_message", "sign_typed_data", "sign_transaction"):
        assert callable(getattr(signer.SignerClient, method, None)), (
            f"SignerClient must expose {method}"
        )


def test_dual_surface_public_contract_and_authority_split(tmp_path: Path) -> None:
    pilot_config = _module("loveengine_witness.pilot_config")
    pilot_surfaces = _module("loveengine_witness.pilot_surfaces")
    _public_symbols(
        pilot_config,
        {
            "PilotConfigV2",
            "load_pilot_config_v2",
            "build_pilot_invite_v2",
            "validate_pilot_invite_v2",
        },
    )
    _public_symbols(
        pilot_surfaces,
        {"PilotSurfaceApps", "PilotSurfaceServer", "create_pilot_surfaces"},
    )

    config = pilot_config.PilotConfigV2(
        schema_version="loveengine.pilot-config/2",
        run_id="contract-run",
        admin=pilot_config.PilotAdminSurfaceConfig(
            host="127.0.0.1",
            port=18780,
            allowed_origins=("http://127.0.0.1:18780",),
        ),
        participant=pilot_config.PilotParticipantSurfaceConfig(
            host="127.0.0.1",
            port=18781,
            public_base_url="https://pilot.example.ts.net",
        ),
        database=tmp_path / "pilot.sqlite",
        relay_database=tmp_path / "relay.sqlite",
        artifact_root=tmp_path / "artifacts",
        audit_log=tmp_path / "audit.jsonl",
        token_file=tmp_path / "operator.token",
        bootstrap_file=tmp_path / "bootstrap.json",
        release_file=tmp_path / "release.json",
        package_archive=tmp_path / "release.zip",
        rpc_url="http://127.0.0.1:8545",
        rpc_url_file=None,
        chain_id="11155111",
        write_token="contract-write-token",
    )
    apps = pilot_surfaces.create_pilot_surfaces(
        config,
        bootstrap={"directory": []},
        releases={},
        package_artifacts={},
        readiness=lambda: (True, {"contract": True}),
    )
    assert apps.admin is not apps.participant

    participant = _route_pairs(apps.participant)
    required_participant_gets = {
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
    }
    assert required_participant_gets.issubset(participant)
    assert not {method for method, _ in participant} - {"GET", "HEAD"}
    participant_paths = {path for _, path in participant}
    assert {
        "/operator/",
        "/v1/metrics",
        "/v1/relay/tasks",
        "/v1/live/sessions",
        "/v1/live/sessions/{session_id}/close",
        "/v1/live/sessions/{session_id}/evidence/finalize",
    }.isdisjoint(participant_paths)

    admin = _route_pairs(apps.admin)
    assert {
        ("GET", "/operator/"),
        ("GET", "/v1/metrics"),
        ("POST", "/v1/relay/tasks"),
        ("POST", "/v1/live/sessions"),
        ("POST", "/v1/live/sessions/{session_id}/events"),
        ("POST", "/v1/live/sessions/{session_id}/close"),
        (
            "POST",
            "/v1/live/sessions/{session_id}/evidence/finalize",
        ),
    }.issubset(admin)


def test_pilot_invite_v2_is_connection_only_and_time_bounded() -> None:
    pilot_config = _module("loveengine_witness.pilot_config")
    build = pilot_config.build_pilot_invite_v2
    required_parameters = {
        "participant_url",
        "chain_id",
        "registry",
        "publisher",
        "version",
        "package_hash",
        "issued_at",
        "expires_at",
    }
    assert required_parameters.issubset(inspect.signature(build).parameters)

    invite = build(
        participant_url="https://pilot.example.ts.net",
        chain_id="11155111",
        registry=ADDRESS_A,
        publisher=ADDRESS_B,
        version="0.7.0-invited-public-pilot",
        package_hash=BYTES32_A,
        issued_at="100",
        expires_at="200",
    )
    assert set(invite) == {
        "schema_version",
        "participant_url",
        "dashboard_url",
        "relay_url",
        "chain_id",
        "registry",
        "publisher",
        "skill_id",
        "version",
        "package_hash",
        "issued_at",
        "expires_at",
    }
    assert invite["schema_version"] == "loveengine.pilot-invite/2"
    assert invite["participant_url"] == "https://pilot.example.ts.net"
    assert invite["dashboard_url"] == "https://pilot.example.ts.net/demo/"
    assert invite["relay_url"] == "wss://pilot.example.ts.net/v1/ws"
    assert int(invite["expires_at"]) > int(invite["issued_at"])
    assert not {
        "operator_url",
        "admin_url",
        "ssh_host",
        "write_token",
        "rpc_url",
        "rpc_credential",
        "signer_endpoint",
        "trust_policy",
    }.intersection(invite)
    assert pilot_config.validate_pilot_invite_v2(invite) == invite

    invalid = dict(invite, expires_at=invite["issued_at"])
    with pytest.raises(LoveEngineError):
        pilot_config.validate_pilot_invite_v2(invalid)


def test_core_transcript_v2_schema_preserves_evidence_boundaries() -> None:
    schema = _schema("witness-core-transcript-v2.schema.json")
    assert schema["properties"]["schema_version"]["const"] == (
        "loveengine.witness-core-transcript/2"
    )
    required = set(schema["required"])
    assert {
        "source_commit",
        "version",
        "protocol",
        "environment",
        "actors_simulated",
        "input_mode",
        "verification_anchor",
        "package",
        "release_anchor",
        "chain",
        "pilot_invite_hash",
        "trust_policy_hash",
        "public_service",
        "signer_evidence",
        "participant_attestations",
        "network_tasks",
        "observation_receipts",
        "review_receipts",
        "proposal_gate",
        "acceptance",
        "does_not_prove",
        "transcript_hash",
    }.issubset(required)
    assert set(schema["properties"]["input_mode"]["enum"]) == {
        "synthetic_fixture",
        "invited_operator_input",
    }
    boundary = schema["properties"]["does_not_prove"]
    assert boundary["type"] == "array"
    assert boundary.get("minItems", 0) >= 1
    assert boundary["items"]["type"] == "string"

    anchor = schema["properties"]["verification_anchor"]
    assert {"block_number", "block_hash", "timestamp", "selected_via"}.issubset(
        set(anchor["required"])
    )
    service = schema["properties"]["public_service"]
    assert {
        "participant_allowlist_hash",
        "tailscale_serve_config_hash",
        "restore_verified",
    }.issubset(set(service["required"]))
    acceptance = schema["properties"]["acceptance"]
    assert acceptance["properties"]["duration_seconds"]["const"] == 900
    assert acceptance["properties"]["event_count"]["const"] == 30
    assert acceptance["properties"]["observer_count"]["const"] == 10


def test_sepolia_anchor_uses_safe_without_latest_fallback() -> None:
    core_transcript = _module("loveengine_witness.core_transcript")
    _public_symbols(
        core_transcript,
        {
            "select_verification_anchor",
            "public_service_config_hash",
            "core_transcript_hash",
            "verify_core_transcript",
        },
    )

    class Eth:
        chain_id = 11155111

        def __init__(self) -> None:
            self.requested: list[str] = []

        def get_block(self, identifier: str) -> dict[str, Any]:
            self.requested.append(identifier)
            assert identifier == "safe"
            return {"number": 7, "hash": bytes.fromhex("12" * 32), "timestamp": 9}

    eth = Eth()
    anchor = core_transcript.select_verification_anchor(
        SimpleNamespace(eth=eth), environment="sepolia_invited_pilot"
    )
    assert eth.requested == ["safe"]
    assert anchor == {
        "block_number": "7",
        "block_hash": "0x" + "12" * 32,
        "timestamp": "9",
        "selected_via": "safe",
    }

    wrong_chain = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    with pytest.raises(LoveEngineError) as error:
        core_transcript.select_verification_anchor(
            wrong_chain, environment="sepolia_invited_pilot"
        )
    assert error.value.code == "wrong_chain_id"


def test_unknown_core_transcript_version_fails_with_stable_public_error() -> None:
    core_transcript = _module("loveengine_witness.core_transcript")
    with pytest.raises(LoveEngineError) as error:
        core_transcript.verify_core_transcript(
            {"schema_version": "loveengine.witness-core-transcript/999"}
        )
    assert error.value.code == "unsupported_schema_version"


def test_public_service_hash_is_secret_free_and_self_field_independent() -> None:
    core_transcript = _module("loveengine_witness.core_transcript")
    service = {
        "schema_version": "loveengine.invited-pilot-service-config/1",
        "transport": "tailscale_serve",
        "participant_origin": "https://pilot.example.ts.net",
        "relay_url": "wss://pilot.example.ts.net/v1/ws",
        "tailnet_only": True,
        "funnel_enabled": False,
        "admin_surface": "loopback_only",
        "participant_allowlist_hash": BYTES32_A,
        "tailscale_serve_config_hash": BYTES32_B,
        "restore_verified": True,
    }
    digest = core_transcript.public_service_config_hash(service)
    assert core_transcript.public_service_config_hash(
        {**service, "config_hash": digest}
    ) == digest
    with pytest.raises(LoveEngineError):
        core_transcript.public_service_config_hash(
            {**service, "write_token": "must-not-enter-transcript"}
        )
