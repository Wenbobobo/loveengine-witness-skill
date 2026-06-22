from __future__ import annotations

import copy

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import keccak256_hex
from loveengine_witness.registry import verify_release


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
