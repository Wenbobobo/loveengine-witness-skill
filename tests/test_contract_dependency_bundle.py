from __future__ import annotations

import hashlib
import json
import shutil
import warnings
import zipfile
from pathlib import Path

import pytest

from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.contract_dependency_bundle import (
    MANIFEST_NAME,
    _zip_info,
    build_contract_dependency_bundle,
    install_contract_dependency_bundle,
)
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.toolchain import (
    CONTRACT_DEPENDENCIES,
    _tree_inventory,
)


def _write_contracts_fixture(root: Path, *, include_lib: bool) -> Path:
    contracts = root / "contracts"
    contracts.mkdir(parents=True)
    entries = []
    if include_lib:
        for index, dependency in enumerate(CONTRACT_DEPENDENCIES):
            dependency_root = contracts / "lib" / dependency["name"]
            (dependency_root / "src").mkdir(parents=True)
            (dependency_root / "LICENSE").write_text(
                f"license-{index}\n",
                encoding="utf-8",
            )
            (dependency_root / "src" / "Fixture.sol").write_text(
                f"pragma solidity 0.8.26; contract Fixture{index} {{}}\n",
                encoding="utf-8",
            )
            inventory = _tree_inventory(
                dependency_root,
                error_code="fixture_invalid",
                detail=dependency["name"],
            )
            entries.append(
                {
                    "name": dependency["name"],
                    "package": dependency["package"],
                    "commit": dependency["commit"],
                    "tree_sha256": inventory["inventory_sha256"],
                }
            )
    else:
        source_lock = root.parent / "source" / "contracts" / "dependency-lock.json"
        shutil.copyfile(source_lock, contracts / "dependency-lock.json")
        return contracts
    (contracts / "dependency-lock.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "loveengine.contract-dependency-lock/1",
                "tree_digest_algorithm": "sha256:canonical-source-tree-lf-v1",
                "dependencies": entries,
            }
        )
    )
    return contracts


def _bundle_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = _write_contracts_fixture(tmp_path / "source", include_lib=True)
    archive = tmp_path / "dependencies.zip"
    built = build_contract_dependency_bundle(archive, contracts_root=source)
    return source, archive, built


def _archive_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_bundle(
    source: Path,
    target: Path,
    *,
    manifest_transform=None,
    member_transform=None,
) -> None:
    with zipfile.ZipFile(source, "r") as bundle:
        values = {info.filename: bundle.read(info) for info in bundle.infolist()}
    if manifest_transform is not None:
        manifest = json.loads(values[MANIFEST_NAME])
        manifest_transform(manifest)
        values[MANIFEST_NAME] = canonical_json_bytes(manifest)
    if member_transform is not None:
        member_transform(values)
    with zipfile.ZipFile(target, "w") as bundle:
        for name in sorted(values, key=lambda value: (value != MANIFEST_NAME, value)):
            bundle.writestr(_zip_info(name), values[name])


def test_dependency_bundle_is_deterministic_and_installs_atomically(
    tmp_path: Path,
) -> None:
    source = _write_contracts_fixture(tmp_path / "source", include_lib=True)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    built = build_contract_dependency_bundle(first, contracts_root=source)
    repeated = build_contract_dependency_bundle(second, contracts_root=source)

    assert first.read_bytes() == second.read_bytes()
    assert built["archive_sha256"] == repeated["archive_sha256"]
    target = _write_contracts_fixture(tmp_path / "target", include_lib=False)
    installed = install_contract_dependency_bundle(
        first,
        expected_sha256=built["archive_sha256"],
        contracts_root=target,
    )
    assert installed["operation"] == "install"
    assert installed["manifest_sha256"] == built["manifest_sha256"]
    for dependency in CONTRACT_DEPENDENCIES:
        assert (
            target / "lib" / dependency["name"] / "src" / "Fixture.sol"
        ).read_bytes() == (
            source / "lib" / dependency["name"] / "src" / "Fixture.sol"
        ).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert not list(target.glob(".loveengine-dependency-bundle-*"))


def test_dependency_bundle_rejects_wrong_archive_hash(tmp_path: Path) -> None:
    _, archive, _ = _bundle_fixture(tmp_path)
    target = _write_contracts_fixture(tmp_path / "target", include_lib=False)

    with pytest.raises(LoveEngineError) as error:
        install_contract_dependency_bundle(
            archive,
            expected_sha256="sha256:" + "0" * 64,
            contracts_root=target,
        )

    assert error.value.code == "contract_dependency_bundle_invalid"
    assert not (target / "lib").exists()


def test_dependency_bundle_rejects_tampered_member_without_partial_install(
    tmp_path: Path,
) -> None:
    _, archive, _ = _bundle_fixture(tmp_path)
    tampered = tmp_path / "tampered.zip"

    def mutate(values: dict[str, bytes]) -> None:
        name = next(name for name in values if name != MANIFEST_NAME)
        values[name] += b"tampered"

    _rewrite_bundle(archive, tampered, member_transform=mutate)
    target = _write_contracts_fixture(tmp_path / "target", include_lib=False)

    with pytest.raises(LoveEngineError) as error:
        install_contract_dependency_bundle(
            tampered,
            expected_sha256=_archive_digest(tampered),
            contracts_root=target,
        )

    assert error.value.code == "contract_dependency_bundle_invalid"
    assert not (target / "lib").exists()
    assert not list(target.glob(".loveengine-dependency-bundle-*"))


def test_dependency_bundle_rejects_path_escape_and_duplicate_members(
    tmp_path: Path,
) -> None:
    _, archive, _ = _bundle_fixture(tmp_path)
    escaped = tmp_path / "escaped.zip"

    def escape(manifest: dict) -> None:
        manifest["files"][0]["path"] = "../outside"

    _rewrite_bundle(archive, escaped, manifest_transform=escape)
    target = _write_contracts_fixture(tmp_path / "target", include_lib=False)
    with pytest.raises(LoveEngineError) as error:
        install_contract_dependency_bundle(
            escaped,
            expected_sha256=_archive_digest(escaped),
            contracts_root=target,
        )
    assert error.value.code == "contract_dependency_bundle_invalid"
    assert not (tmp_path / "outside").exists()

    duplicate = tmp_path / "duplicate.zip"
    shutil.copyfile(archive, duplicate)
    with zipfile.ZipFile(duplicate, "a") as bundle, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        first = next(name for name in bundle.namelist() if name != MANIFEST_NAME)
        bundle.writestr(_zip_info(first), b"duplicate")
    with pytest.raises(LoveEngineError) as duplicate_error:
        install_contract_dependency_bundle(
            duplicate,
            expected_sha256=_archive_digest(duplicate),
            contracts_root=target,
        )
    assert duplicate_error.value.code == "contract_dependency_bundle_invalid"


def test_dependency_bundle_never_overwrites_existing_dependency_tree(
    tmp_path: Path,
) -> None:
    _, archive, built = _bundle_fixture(tmp_path)
    target = _write_contracts_fixture(tmp_path / "target", include_lib=False)
    sentinel = target / "lib" / "operator-owned.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep\n", encoding="utf-8")

    with pytest.raises(LoveEngineError) as error:
        install_contract_dependency_bundle(
            archive,
            expected_sha256=built["archive_sha256"],
            contracts_root=target,
        )

    assert error.value.code == "contract_dependency_bundle_target_exists"
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
