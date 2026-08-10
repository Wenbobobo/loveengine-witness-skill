from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness import pilot_config as pilot_config_module
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import (
    build_bootstrap_v2,
    build_node_profile_v2,
)
from loveengine_witness.m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
)
from loveengine_witness.network_typed_data import id_hash
from loveengine_witness.pilot_config import (
    build_pilot_invite,
    build_pilot_invite_v2,
    default_pilot_readiness,
    load_pilot_config,
    load_pilot_config_v2,
    load_pilot_rpc_url,
    validate_pilot_publication,
    validate_pilot_invite_v2,
)


TOKEN = "pilot-config-test-token"


def _token(tmp_path: Path) -> Path:
    path = tmp_path / "operator.token"
    path.write_text(TOKEN, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _rpc_url_file(tmp_path: Path) -> Path:
    path = tmp_path / "sepolia-rpc-url.txt"
    path.write_text(
        "https://sepolia.example.invalid/v3/private-key",
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _v2_value(tmp_path: Path) -> dict:
    token = _token(tmp_path)
    rpc_url_file = _rpc_url_file(tmp_path)
    return {
        "schema_version": "loveengine.pilot-config/2",
        "run_id": "invited-pilot-config-test",
        "admin": {
            "host": "127.0.0.1",
            "port": 8780,
            "allowed_origins": ["http://127.0.0.1:8780"],
        },
        "participant": {
            "host": "127.0.0.1",
            "port": 8781,
            "public_base_url": "https://witness.example-tailnet.ts.net",
        },
        "database": "pilot.sqlite",
        "relay_database": "relay.sqlite",
        "artifact_root": "artifacts",
        "audit_log": "audit.jsonl",
        "token_file": token.name,
        "bootstrap_file": "bootstrap.json",
        "release_file": "release.json",
        "package_archive": "release.zip",
        "rpc_url_file": rpc_url_file.name,
        "chain_id": "11155111",
    }


def _write_v2(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "pilot-config-v2.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _sign(account: object, typed_data: dict) -> str:
    value = Account.sign_message(
        encode_typed_data(full_message=typed_data), account.key
    ).signature.hex()
    return value if value.startswith("0x") else "0x" + value


def _publication_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, dict]:
    chain_id = "11155111"
    registry = "0x" + "1" * 40
    publisher = Account.create()
    node = Account.create()
    profile_value = build_node_profile_v2(
        node=node.address,
        capabilities=["observe_live_text"],
        sequence="1",
        valid_until="4102444800",
    )
    profile = {
        "schema_version": "loveengine.signed-agent-node-profile/2",
        "chain_id": chain_id,
        "registry": registry,
        "profile": profile_value,
        "signature": _sign(
            node,
            build_node_profile_v2_typed_data(chain_id, registry, profile_value),
        ),
    }
    bootstrap = build_bootstrap_v2(
        chain_id=chain_id,
        registry=registry,
        publisher=publisher.address,
        nodes=[profile],
        sequence="1",
        valid_until="4102444800",
    )
    bootstrap["signature"] = _sign(
        publisher, build_bootstrap_v2_typed_data(bootstrap)
    )
    package_hash = "0x" + "a" * 64
    manifest_hash = "0x" + "b" * 64
    release = {
        "schema_version": "loveengine.skill-release/1",
        "chain_id": chain_id,
        "registry": registry,
        "publisher": publisher.address,
        "skill_id": "loveengine-witness",
        "version": "0.7.0-invited-public-pilot",
        "version_hash": id_hash("0.7.0-invited-public-pilot"),
        "package_hash": package_hash,
        "manifest_hash": manifest_hash,
        "previous_version_hash": "0x" + "0" * 64,
        "status": "active",
    }
    (tmp_path / "bootstrap.json").write_text(
        json.dumps(bootstrap), encoding="utf-8"
    )
    (tmp_path / "release.json").write_text(json.dumps(release), encoding="utf-8")
    (tmp_path / "release.zip").write_bytes(b"package fixture")
    package = {
        "skill_id": release["skill_id"],
        "version": release["version"],
        "archive_keccak256": package_hash,
        "manifest_keccak256": manifest_hash,
    }

    def verify_fixture(path: Path, *, expected_package_hash: str) -> dict:
        assert path == tmp_path / "release.zip"
        assert expected_package_hash == package_hash
        return package

    monkeypatch.setattr(pilot_config_module, "verify_package", verify_fixture)
    config = load_pilot_config_v2(_write_v2(tmp_path, _v2_value(tmp_path)))
    return config, package


def test_pilot_config_v1_loader_and_invite_remain_unchanged(tmp_path: Path) -> None:
    token = _token(tmp_path)
    value = {
        "schema_version": "loveengine.pilot-config/1",
        "run_id": "v1",
        "host": "127.0.0.1",
        "port": 8780,
        "database": "pilot.sqlite",
        "relay_database": "relay.sqlite",
        "artifact_root": "artifacts",
        "audit_log": "audit.jsonl",
        "token_file": token.name,
        "bootstrap_file": "bootstrap.json",
        "release_file": "release.json",
        "package_archive": "release.zip",
        "allowed_origin": "http://127.0.0.1:8780",
        "rpc_url": "http://127.0.0.1:8545",
        "chain_id": "31337",
        "allow_all_interfaces": False,
    }
    path = tmp_path / "pilot-config-v1.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    config = load_pilot_config(path)
    invite = build_pilot_invite(
        base_url="http://127.0.0.1:8780",
        chain_id="31337",
        registry="0x" + "1" * 40,
        publisher="0x" + "2" * 40,
        version="0.6.1-contract-public-pilot",
        package_hash="0x" + "3" * 64,
    )

    assert config.schema_version == "loveengine.pilot-config/1"
    assert config.write_token == TOKEN
    assert invite["schema_version"] == "loveengine.pilot-invite/1"
    assert invite["operator_url"].endswith("/operator/")


def test_pilot_config_v2_loads_two_loopback_surfaces_without_secret_repr(
    tmp_path: Path,
) -> None:
    config = load_pilot_config_v2(_write_v2(tmp_path, _v2_value(tmp_path)))

    assert config.admin.port == 8780
    assert config.participant.port == 8781
    assert config.participant.public_base_url.endswith(".ts.net")
    assert config.write_token == TOKEN
    assert config.rpc_url is None
    assert config.rpc_url_file == tmp_path / "sepolia-rpc-url.txt"
    assert load_pilot_rpc_url(config).endswith("/v3/private-key")
    assert TOKEN not in repr(config)
    assert "private-key" not in repr(config)


def test_pilot_v2_has_strict_local_anvil_transport_mode(tmp_path: Path) -> None:
    value = _v2_value(tmp_path)
    value["chain_id"] = "31337"
    value.pop("rpc_url_file")
    value["rpc_url"] = "http://127.0.0.1:8545"
    value["participant"]["public_base_url"] = "http://127.0.0.1:8781"

    config = load_pilot_config_v2(_write_v2(tmp_path, value))
    invite = build_pilot_invite_v2(
        participant_url="http://127.0.0.1:8781",
        chain_id="31337",
        registry="0x" + "1" * 40,
        publisher="0x" + "2" * 40,
        version="0.7.0-invited-public-pilot",
        package_hash="0x" + "3" * 64,
        issued_at="1780000000",
        expires_at="1780000900",
    )

    assert config.participant.public_base_url == "http://127.0.0.1:8781"
    assert load_pilot_rpc_url(config) == "http://127.0.0.1:8545"
    assert invite["relay_url"] == "ws://127.0.0.1:8781/v1/ws"


def test_pilot_v2_rejects_direct_sepolia_rpc_without_leaking_url(
    tmp_path: Path,
) -> None:
    value = _v2_value(tmp_path)
    value.pop("rpc_url_file")
    secret_url = "https://sepolia.example.invalid/v3/private-key"
    value["rpc_url"] = secret_url

    with pytest.raises(LoveEngineError) as caught:
        load_pilot_config_v2(_write_v2(tmp_path, value))

    assert caught.value.code == "rpc_url_file_required"
    assert secret_url not in str(caught.value)


def test_pilot_v2_rejects_world_readable_sepolia_rpc_file(
    tmp_path: Path,
) -> None:
    value = _v2_value(tmp_path)
    rpc_file = tmp_path / value["rpc_url_file"]
    if os.name == "nt":
        pytest.skip("POSIX mode enforcement is not available on Windows")
    rpc_file.chmod(0o644)

    with pytest.raises(LoveEngineError) as caught:
        load_pilot_config_v2(_write_v2(tmp_path, value))

    assert caught.value.code == "restricted_file_permissions"
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value["participant"].update(port=8780), "pilot_surface_port_conflict"),
        (lambda value: value["admin"].update(host="0.0.0.0"), "schema_validation_failed"),
        (
            lambda value: value["admin"].update(
                allowed_origins=["https://operator.example.com"]
            ),
            "invalid_admin_origin",
        ),
        (
            lambda value: value["participant"].update(
                public_base_url="https://public.example.com"
            ),
            "schema_validation_failed",
        ),
        (
            lambda value: value["participant"].update(
                public_base_url="https://witness.example-tailnet.ts.net:8443"
            ),
            "schema_validation_failed",
        ),
    ],
)
def test_pilot_config_v2_rejects_boundary_weakening(
    tmp_path: Path, mutation: object, code: str
) -> None:
    value = _v2_value(tmp_path)
    mutation(value)

    with pytest.raises(LoveEngineError) as caught:
        load_pilot_config_v2(_write_v2(tmp_path, value))

    assert caught.value.code == code


def test_pilot_invite_v2_contains_discovery_only_and_explicit_lifetime() -> None:
    invite = build_pilot_invite_v2(
        participant_url="https://witness.example-tailnet.ts.net",
        chain_id="11155111",
        registry="0x" + "1" * 40,
        publisher="0x" + "2" * 40,
        version="0.7.0-invited-public-pilot",
        package_hash="0x" + "3" * 64,
        issued_at="1780000000",
        expires_at="1780000900",
    )

    assert invite["relay_url"] == "wss://witness.example-tailnet.ts.net/v1/ws"
    assert invite["dashboard_url"].endswith("/demo/")
    assert invite["issued_at"] == "1780000000"
    assert invite["expires_at"] == "1780000900"
    serialized = json.dumps(invite).lower()
    assert "operator" not in serialized
    assert "admin" not in serialized
    assert "token" not in serialized
    assert "manifest_hash" not in invite


def test_pilot_invite_v2_rejects_non_default_sepolia_https_port() -> None:
    with pytest.raises(LoveEngineError) as caught:
        build_pilot_invite_v2(
            participant_url="https://witness.example-tailnet.ts.net:8443",
            chain_id="11155111",
            registry="0x" + "1" * 40,
            publisher="0x" + "2" * 40,
            version="0.7.0-invited-public-pilot",
            package_hash="0x" + "3" * 64,
            issued_at="1780000000",
            expires_at="1780000900",
        )

    assert caught.value.code == "invalid_participant_origin"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("participant_url", "http://witness.example-tailnet.ts.net"),
        ("dashboard_url", "https://other.example-tailnet.ts.net/demo/"),
        ("relay_url", "ws://witness.example-tailnet.ts.net/v1/ws"),
        ("expires_at", "1780000000"),
    ],
)
def test_pilot_invite_v2_rejects_downgrade_cross_origin_and_empty_lifetime(
    field: str, value: str
) -> None:
    invite = build_pilot_invite_v2(
        participant_url="https://witness.example-tailnet.ts.net",
        chain_id="11155111",
        registry="0x" + "1" * 40,
        publisher="0x" + "2" * 40,
        version="0.7.0-invited-public-pilot",
        package_hash="0x" + "3" * 64,
        issued_at="1780000000",
        expires_at="1780000900",
    )
    invite[field] = value

    with pytest.raises(LoveEngineError):
        validate_pilot_invite_v2(invite)


def test_pilot_invite_v2_rejects_unknown_schema_with_stable_error() -> None:
    with pytest.raises(LoveEngineError) as caught:
        validate_pilot_invite_v2(
            {"schema_version": "loveengine.pilot-invite/999"}
        )

    assert caught.value.code == "unsupported_schema_version"


def test_pilot_publication_binds_signed_bootstrap_release_and_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)

    bootstrap, release, archive = validate_pilot_publication(config)

    assert bootstrap["chain_id"] == config.chain_id
    assert release["status"] == "active"
    assert archive == b"package fixture"


def test_pilot_publication_rejects_release_registry_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)
    release = json.loads(config.release_file.read_text(encoding="utf-8"))
    release["registry"] = "0x" + "9" * 40
    config.release_file.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(LoveEngineError) as caught:
        validate_pilot_publication(config)

    assert caught.value.code == "publication_registry_mismatch"


def test_pilot_publication_rejects_package_manifest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, package = _publication_fixture(tmp_path, monkeypatch)
    package["manifest_keccak256"] = "0x" + "d" * 64

    with pytest.raises(LoveEngineError) as caught:
        validate_pilot_publication(config)

    assert caught.value.code == "publication_manifest_mismatch"


def test_pilot_publication_rejects_release_version_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)
    release = json.loads(config.release_file.read_text(encoding="utf-8"))
    release["version_hash"] = "0x" + "d" * 64
    config.release_file.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(LoveEngineError) as caught:
        validate_pilot_publication(config)

    assert caught.value.code == "publication_version_mismatch"


def test_pilot_readiness_fails_closed_for_tampered_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)
    bootstrap = json.loads(config.bootstrap_file.read_text(encoding="utf-8"))
    bootstrap["directory_hash"] = "0x" + "0" * 64
    config.bootstrap_file.write_text(json.dumps(bootstrap), encoding="utf-8")

    class ReadyEth:
        chain_id = 11155111

    class ReadyWeb3:
        def __init__(self, provider: object) -> None:
            self.eth = ReadyEth()

    monkeypatch.setattr(pilot_config_module, "Web3", ReadyWeb3)
    ready, checks = default_pilot_readiness(config)

    assert ready is False
    assert checks["chain"] is True
    assert checks["publication"] is False
    assert checks["publication_error"] == "directory_hash_mismatch"
    assert "private-key" not in json.dumps(checks)


def test_pilot_readiness_requires_active_onchain_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)

    class ReadyEth:
        chain_id = 11155111

    class ReadyWeb3:
        def __init__(self, provider: object) -> None:
            self.eth = ReadyEth()

    monkeypatch.setattr(pilot_config_module, "Web3", ReadyWeb3)
    monkeypatch.setattr(
        pilot_config_module,
        "verify_onchain_release",
        lambda *_args, **_kwargs: {"valid": True, "status": "active"},
    )

    ready, checks = default_pilot_readiness(config)

    assert ready is True
    assert checks["registry_release"] is True


def test_pilot_readiness_fails_closed_when_registry_release_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _publication_fixture(tmp_path, monkeypatch)

    class ReadyEth:
        chain_id = 11155111

    class ReadyWeb3:
        def __init__(self, provider: object) -> None:
            self.eth = ReadyEth()

    monkeypatch.setattr(pilot_config_module, "Web3", ReadyWeb3)

    def missing_release(*_args: object, **_kwargs: object) -> dict:
        raise LoveEngineError("release_not_found", "fixture")

    monkeypatch.setattr(
        pilot_config_module, "verify_onchain_release", missing_release
    )

    ready, checks = default_pilot_readiness(config)

    assert ready is False
    assert checks["registry_release"] is False
    assert checks["registry_release_error"] == "release_not_found"
    assert "private-key" not in json.dumps(checks)
