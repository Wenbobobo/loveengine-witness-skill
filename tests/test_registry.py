from __future__ import annotations

import copy

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import keccak256_hex
from loveengine_witness.registry import verify_onchain_release, verify_release


def test_release_verification_rejects_wrong_publisher_hash_and_status(tmp_path) -> None:
    artifact = tmp_path / "package.json"
    artifact.write_bytes(b'{"skill_id":"loveengine-witness"}')
    release = {
        "schema_version": "loveengine.skill-release/1",
        "chain_id": "31337",
        "registry": "0x" + "1" * 40,
        "publisher": "0x" + "2" * 40,
        "skill_id": "loveengine-witness",
        "version": "0.3.0-network-pilot",
        "version_hash": "0x" + "3" * 64,
        "package_hash": keccak256_hex(artifact.read_bytes()),
        "manifest_hash": "0x" + "4" * 64,
        "previous_version_hash": "0x" + "0" * 64,
        "replacement_version_hash": "0x" + "0" * 64,
        "status": "active",
    }
    assert verify_release(
        release,
        artifact,
        expected_chain_id="31337",
        expected_registry=release["registry"],
        expected_publisher=release["publisher"],
    )["valid"]

    for field, value, code in (
        ("publisher", "0x" + "8" * 40, "wrong_publisher"),
        ("package_hash", "0x" + "8" * 64, "package_hash_mismatch"),
        ("status", "deprecated", "release_not_active"),
        ("status", "revoked", "release_not_active"),
    ):
        changed = copy.deepcopy(release)
        changed[field] = value
        with pytest.raises(LoveEngineError) as error:
            verify_release(
                changed,
                artifact,
                expected_chain_id="31337",
                expected_registry=release["registry"],
                expected_publisher=release["publisher"],
            )
        assert error.value.code == code


class _FakeReleaseCall:
    def __init__(self, release: tuple[object, ...]) -> None:
        self.release = release

    def call(self) -> tuple[object, ...]:
        return self.release


class _FakeFunctions:
    def __init__(self, release: tuple[object, ...]) -> None:
        self.release = release
        self.arguments: tuple[object, ...] | None = None

    def getRelease(self, *arguments: object) -> _FakeReleaseCall:
        self.arguments = arguments
        return _FakeReleaseCall(self.release)


class _FakeContract:
    def __init__(self, release: tuple[object, ...]) -> None:
        self.functions = _FakeFunctions(release)


class _FakeEth:
    def __init__(self, release: tuple[object, ...], chain_id: int = 31337) -> None:
        self.chain_id = chain_id
        self.release = release

    def get_code(self, address: str) -> bytes:
        return b"\x60\x00"

    def contract(self, *, address: str, abi: list[dict[str, object]]) -> _FakeContract:
        return _FakeContract(self.release)


class _FakeWeb3:
    def __init__(self, release: tuple[object, ...], chain_id: int = 31337) -> None:
        self.eth = _FakeEth(release, chain_id)


def test_onchain_release_binds_rpc_registry_package_and_manifest(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_hash = "0x" + "11" * 32
    manifest_hash = "0x" + "22" * 32
    release = (
        bytes.fromhex("11" * 32),
        bytes.fromhex("22" * 32),
        bytes(32),
        bytes(32),
        1,
        1770000000,
    )
    artifact = tmp_path / "package.zip"
    artifact.write_bytes(b"fixture")
    observed: dict[str, object] = {}

    def fake_verify_package(path, *, expected_package_hash=None, integrity_only=False):
        observed["path"] = path
        observed["expected_package_hash"] = expected_package_hash
        return {
            "manifest_keccak256": manifest_hash,
            "skill_id": "loveengine-witness",
            "version": "0.6.1-contract-public-pilot",
            "checked_file_count": 42,
        }

    monkeypatch.setattr("loveengine_witness.package.verify_package", fake_verify_package)
    result = verify_onchain_release(
        artifact,
        rpc_url="http://127.0.0.1:8545",
        expected_chain_id="31337",
        registry="0x" + "1" * 40,
        publisher="0x" + "2" * 40,
        skill_id="loveengine-witness",
        version="0.6.1-contract-public-pilot",
        web3=_FakeWeb3(release),
    )
    assert result["verification_level"] == "chain_verified"
    assert result["trust_bound"] is True
    assert result["package_hash"] == package_hash
    assert observed == {
        "path": artifact,
        "expected_package_hash": package_hash,
    }


@pytest.mark.parametrize(
    ("status", "manifest", "chain_id", "code"),
    [
        (2, bytes.fromhex("22" * 32), 31337, "release_not_active"),
        (1, bytes.fromhex("33" * 32), 31337, "manifest_hash_mismatch"),
        (1, bytes.fromhex("22" * 32), 1, "wrong_chain_id"),
    ],
)
def test_onchain_release_rejects_untrusted_chain_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    manifest: bytes,
    chain_id: int,
    code: str,
) -> None:
    release = (
        bytes.fromhex("11" * 32),
        manifest,
        bytes(32),
        bytes(32),
        status,
        1770000000,
    )
    monkeypatch.setattr(
        "loveengine_witness.package.verify_package",
        lambda *args, **kwargs: {
            "manifest_keccak256": "0x" + "22" * 32,
            "skill_id": "loveengine-witness",
            "version": "0.6.1-contract-public-pilot",
            "checked_file_count": 42,
        },
    )
    with pytest.raises(LoveEngineError) as error:
        verify_onchain_release(
            tmp_path / "package.zip",
            rpc_url="http://127.0.0.1:8545",
            expected_chain_id="31337",
            registry="0x" + "1" * 40,
            publisher="0x" + "2" * 40,
            skill_id="loveengine-witness",
            version="0.6.1-contract-public-pilot",
            web3=_FakeWeb3(release, chain_id),
        )
    assert error.value.code == code
