from __future__ import annotations

import json
import subprocess
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

import loveengine_witness.cli_pilot as cli_pilot
import loveengine_witness.pilot_chain as pilot_chain
import loveengine_witness.pilot_runtime as pilot_runtime
import loveengine_witness.toolchain as toolchain
from loveengine_witness.cli import build_parser, dispatch
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.toolchain import (
    CONTRACT_DEPENDENCIES,
    DEPENDENCY_LOCK_FILENAME,
    PREPARATION_ATTESTATION_FILENAME,
    REQUIRED_CONTRACT_ARTIFACTS,
    _tree_inventory,
    prepare_contracts,
    verify_prepared_contract_artifacts,
)


def _write_contract_project(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    (contracts / "src").mkdir(parents=True)
    for name in REQUIRED_CONTRACT_ARTIFACTS:
        (contracts / "src" / f"{name}.sol").write_text(
            f"pragma solidity 0.8.26;\ncontract {name} {{}}\n",
            encoding="utf-8",
        )
    (contracts / "foundry.toml").write_text(
        """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
""",
        encoding="utf-8",
    )
    (contracts / "remappings.txt").write_text(
        "forge-std/=lib/forge-std/src/\n"
        "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/\n",
        encoding="utf-8",
    )
    return contracts


def _write_dependency(contracts: Path, name: str) -> None:
    root = contracts / "lib" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "LICENSE").write_text(name + "\n", encoding="utf-8")
    source = root / "src" / "Fixture.sol"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "pragma solidity 0.8.26;\ncontract Fixture {}\n",
        encoding="utf-8",
    )


def _write_dependency_lock(contracts: Path) -> None:
    entries = []
    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        inventory = _tree_inventory(
            contracts / "lib" / name,
            error_code="fixture_error",
            detail=name,
        )
        entries.append(
            {
                "name": name,
                "package": dependency["package"],
                "commit": dependency["commit"],
                "tree_sha256": inventory["inventory_sha256"],
            }
        )
    (contracts / DEPENDENCY_LOCK_FILENAME).write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "loveengine.contract-dependency-lock/1",
                "tree_digest_algorithm": "sha256:canonical-source-tree-lf-v1",
                "dependencies": entries,
            }
        )
    )


def _write_artifacts(contracts: Path) -> None:
    for index, name in enumerate(REQUIRED_CONTRACT_ARTIFACTS, start=1):
        path = contracts / "out" / f"{name}.sol" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""{{
  "abi": [],
  "bytecode": {{"object": "0x{index:02x}"}},
  "deployedBytecode": {{"object": "0x{index + 16:02x}"}},
  "metadata": {{"settings": {{"compilationTarget": {{"src/{name}.sol": "{name}"}}}}, "sources": {{"src/{name}.sol": {{}}}}}}
}}
""",
            encoding="utf-8",
        )


def _binaries(tmp_path: Path) -> dict[str, Path]:
    binaries = {name: tmp_path / name for name in ("forge", "anvil")}
    for binary in binaries.values():
        binary.write_text("fixture", encoding="utf-8")
    return binaries


def _prepare_fixture(tmp_path: Path) -> Path:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )
    return contracts


def test_prepare_contracts_installs_only_missing_dependencies_and_builds_once(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    _write_dependency(contracts, "forge-std")
    _write_dependency(contracts, "openzeppelin-contracts")
    _write_dependency_lock(contracts)
    shutil.rmtree(contracts / "lib" / "openzeppelin-contracts")
    binaries = _binaries(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert cwd == contracts
        calls.append(command)
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{name} Version: 1.7.1\nCommit SHA: fixture\n",
            )
        if command[1] == "install":
            package = command[2]
            if package.startswith("OpenZeppelin/openzeppelin-contracts@"):
                _write_dependency(contracts, "openzeppelin-contracts")
            return subprocess.CompletedProcess(command, 0, stdout="installed\n")
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    result = prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert calls == [
        [str(binaries["forge"]), "--version"],
        [str(binaries["anvil"]), "--version"],
        [
            str(binaries["forge"]),
            "install",
            "OpenZeppelin/openzeppelin-contracts@rev="
            + CONTRACT_DEPENDENCIES[1]["commit"],
            "--no-git",
        ],
        [str(binaries["forge"]), "build", "--force", "--threads", "1"],
    ]
    assert result["schema_version"] == "loveengine.contract-preparation/1"
    assert result["prepared"] is True
    assert result["toolchain"]["forge"]["version"] == "forge Version: 1.7.1"
    assert result["dependencies"][0]["installed"] is False
    assert result["dependencies"][1]["installed"] is True
    assert result["contract_source_sha256"].startswith("sha256:")
    assert result["artifacts"]["count"] == len(REQUIRED_CONTRACT_ARTIFACTS)
    assert result["artifacts"]["inventory_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    ("binary_name", "version_output"),
    (
        ("forge", "forge Version: 1.7.0\nraw-tool-secret\n"),
        ("anvil", "wrapper output\nanvil Version: 1.7.1\nraw-tool-secret\n"),
    ),
)
def test_prepare_contracts_rejects_unpinned_or_wrapped_versions_without_output(
    tmp_path: Path,
    binary_name: str,
    version_output: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    binaries = _binaries(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        name = Path(command[0]).name
        output = (
            version_output
            if name == binary_name
            else f"{name} Version: 1.7.1\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "foundry_version_mismatch"
    assert "raw-tool-secret" not in error.value.message
    assert all("install" not in command for command in calls)
    assert all("build" not in command for command in calls)


def test_prepare_contracts_hides_failed_build_output(tmp_path: Path) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="raw stdout secret",
            stderr="raw stderr secret",
        )

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_build_failed"
    assert error.value.message == "pinned Forge build failed"
    assert "secret" not in error.value.message


def test_prepare_contracts_fails_closed_when_build_leaves_no_artifacts(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout="build claimed success")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_artifact_missing"
    assert "pilot contracts prepare" in error.value.message


@pytest.mark.parametrize(
    ("lock_bytes", "expected_code"),
    (
        (None, "contract_dependency_lock_missing"),
        (b"{}", "contract_dependency_lock_invalid"),
    ),
)
def test_prepare_contracts_rejects_missing_or_malformed_dependency_lock(
    tmp_path: Path,
    lock_bytes: bytes | None,
    expected_code: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    if lock_bytes is not None:
        (contracts / DEPENDENCY_LOCK_FILENAME).write_bytes(lock_bytes)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert command[1:] == ["--version"]
        name = Path(command[0]).name
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{name} Version: 1.7.1\n",
        )

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    (
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "../outside"
libs = ["lib"]
""",
        ),
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
remappings = ["forge-std/=../outside/"]
""",
        ),
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
auto_detect_remappings = true
""",
        ),
        (
            "remappings.txt",
            "forge-std/=../outside/\n"
            "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/\n",
        ),
    ),
)
def test_prepare_rejects_escaping_foundry_configuration_before_running_foundry(
    tmp_path: Path,
    relative_path: str,
    contents: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / relative_path).write_text(contents, encoding="utf-8")
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_foundry_config_invalid"
    assert calls == []


def test_prepare_rejects_unlocked_dependency_directory_before_running_foundry(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / "lib" / "evil").mkdir()
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_layout_invalid"
    assert calls == []


def test_reparse_point_fallback_is_treated_as_a_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReparsePoint:
        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return False

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(st_file_attributes=0x400)

    monkeypatch.setattr(toolchain.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    assert toolchain._is_link_or_reparse_point(ReparsePoint())


def test_safe_tree_walk_rejects_nested_reparse_before_descending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "fixture.sol").write_text("contract Fixture {}\n", encoding="utf-8")
    original = toolchain._is_link_or_reparse_point

    def is_link_or_reparse(path: Path) -> bool:
        return Path(path) == nested or original(Path(path))

    monkeypatch.setattr(toolchain, "_is_link_or_reparse_point", is_link_or_reparse)

    with pytest.raises(LoveEngineError) as error:
        toolchain._safe_tree_files(
            root,
            error_code="contract_path_symlink",
            detail="fixture",
        )

    assert error.value.code == "contract_path_symlink"


def test_safe_tree_walk_checks_skipped_git_directory_before_ignoring_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    git_metadata = root / ".git"
    git_metadata.mkdir(parents=True)
    (git_metadata / "config").write_text("[core]\n", encoding="utf-8")
    original = toolchain._is_link_or_reparse_point

    def is_link_or_reparse(path: Path) -> bool:
        return Path(path) == git_metadata or original(Path(path))

    monkeypatch.setattr(toolchain, "_is_link_or_reparse_point", is_link_or_reparse)

    with pytest.raises(LoveEngineError) as error:
        toolchain._safe_tree_files(
            root,
            error_code="contract_path_symlink",
            detail="fixture",
            skipped_names=frozenset({".git"}),
        )

    assert error.value.code == "contract_path_symlink"


def test_prepare_accepts_lock_matching_dependency_text_without_rewriting_it(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    crlf_file = contracts / "lib" / "forge-std" / "src" / "Fixture.sol"
    crlf_file.write_bytes(b"pragma solidity 0.8.26;\r\ncontract Fixture {}\r\n")
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert crlf_file.read_bytes() == b"pragma solidity 0.8.26;\ncontract Fixture {}\n"
    assert verify_prepared_contract_artifacts(contracts)["count"] == 5


def test_prepare_accepts_lock_matching_extensionless_dependency_text(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    extensionless = contracts / "lib" / "openzeppelin-contracts" / ".gitmodules"
    extensionless.write_bytes(b"[submodule \"fixture\"]\r\npath = fixture\r\n")
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert extensionless.read_bytes() == b"[submodule \"fixture\"]\npath = fixture\n"


def test_prepare_refreshes_only_an_explicitly_requested_mismatched_dependency(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / "lib" / "forge-std" / "LICENSE").write_text(
        "wrong tree\n",
        encoding="utf-8",
    )
    binaries = _binaries(tmp_path)
    install_cwds: list[Path] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        if command[1] == "install":
            install_cwds.append(cwd)
            _write_dependency(cwd, "forge-std")
            (cwd / "lib" / "forge-std" / "src" / "Fixture.sol").write_bytes(
                b"pragma solidity 0.8.26;\r\ncontract Fixture {}\r\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout="installed\n")
        assert cwd == contracts
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    with pytest.raises(LoveEngineError) as rejected:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )
    assert rejected.value.code == "contract_dependency_lock_mismatch"

    result = prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
        refresh_dependencies=True,
    )

    assert len(install_cwds) == 1
    assert install_cwds[0] != contracts
    assert result["refreshed_dependencies"] == ["forge-std"]
    assert (
        contracts / "lib" / "forge-std" / "src" / "Fixture.sol"
    ).read_bytes() == b"pragma solidity 0.8.26;\ncontract Fixture {}\n"


def test_prepare_preserves_refresh_backup_when_build_fails(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    original = contracts / "lib" / "forge-std" / "LICENSE"
    original.write_text("wrong tree\n", encoding="utf-8")
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        if command[1] == "install":
            _write_dependency(cwd, "forge-std")
            return subprocess.CompletedProcess(command, 0, stdout="installed\n")
        assert cwd == contracts
        return subprocess.CompletedProcess(command, 1, stderr="build failed")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
            refresh_dependencies=True,
        )

    assert error.value.code == "contract_build_failed"
    backups = list((contracts / "lib").glob(".loveengine-forge-std-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "LICENSE").read_text(encoding="utf-8") == "wrong tree\n"


def test_prepare_restores_backup_after_interrupted_dependency_swap(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    target = contracts / "lib" / "forge-std"
    backup = contracts / "lib" / (
        ".loveengine-forge-std-backup-" + "a" * 32
    )
    target.rename(backup)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert target.is_dir()
    assert not backup.exists()


def test_prepare_cleans_verified_backup_after_interrupted_refresh(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    backup = contracts / "lib" / (
        ".loveengine-forge-std-backup-" + "b" * 32
    )
    shutil.copytree(contracts / "lib" / "forge-std", backup)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert not backup.exists()


def test_prepare_rejects_artifact_with_unattested_compiler_source(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        _write_artifacts(contracts)
        artifact_path = contracts / "out" / "WitnessDAO.sol" / "WitnessDAO.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["metadata"]["sources"]["test/Injected.sol"] = {}
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_artifact_invalid"


def test_prepare_rejects_ambiguous_interrupted_refresh_backup(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    source = contracts / "lib" / "forge-std"
    for suffix in ("c" * 32, "d" * 32):
        shutil.copytree(
            source,
            contracts / "lib" / f".loveengine-forge-std-backup-{suffix}",
        )
    binaries = _binaries(tmp_path)

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=lambda command, cwd: subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{Path(command[0]).name} Version: 1.7.1\n",
            ),
        )

    assert error.value.code == "contract_dependency_refresh_recovery_required"


def test_verify_requires_canonical_preparation_attestation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    attestation = contracts / "cache" / PREPARATION_ATTESTATION_FILENAME
    attestation.unlink()

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_missing"


def test_verify_rejects_tampered_preparation_attestation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    attestation = contracts / "cache" / PREPARATION_ATTESTATION_FILENAME
    attestation.write_bytes(attestation.read_bytes() + b"\n")

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_invalid"


def test_verify_rejects_swapped_artifact_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    target = contracts / "out" / "WitnessDAO.sol" / "WitnessDAO.json"
    source = contracts / "out" / "PublicSink.sol" / "PublicSink.json"
    target.write_bytes(source.read_bytes())

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_artifact_invalid"


def test_verify_rejects_dependency_tree_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    (contracts / "lib" / "forge-std" / "LICENSE").write_text(
        "replaced\n",
        encoding="utf-8",
    )

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_dependency_lock_mismatch"


def test_verify_rejects_dependency_lock_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    lock = contracts / DEPENDENCY_LOCK_FILENAME
    lock.write_bytes(lock.read_bytes() + b"\n")

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_mismatch"


def test_verify_rejects_source_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    (contracts / "src" / "Witness.sol").write_text(
        "pragma solidity 0.8.26; contract Replaced {}\n",
        encoding="utf-8",
    )

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_mismatch"


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable in this test environment")


@pytest.mark.parametrize("path_kind", ("root", "ancestor", "out", "lib", "cache"))
def test_verify_rejects_contract_path_symlinks(
    tmp_path: Path,
    path_kind: str,
) -> None:
    contracts = _prepare_fixture(tmp_path)
    if path_kind == "root":
        linked = tmp_path / "contracts-link"
        _symlink_or_skip(linked, contracts)
        candidate = linked
    elif path_kind == "ancestor":
        linked = tmp_path / "parent-link"
        _symlink_or_skip(linked, tmp_path)
        candidate = linked / "contracts"
    else:
        original = contracts / path_kind
        moved = tmp_path / f"{path_kind}-target"
        original.rename(moved)
        _symlink_or_skip(original, moved)
        candidate = contracts

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(candidate)

    assert error.value.code == "contract_path_symlink"


@pytest.mark.parametrize("path_kind", ("root", "ancestor", "out", "lib", "cache"))
def test_prepare_rejects_contract_path_symlinks_before_running_foundry(
    tmp_path: Path,
    path_kind: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    for directory in ("out", "cache"):
        (contracts / directory).mkdir()
    if path_kind == "root":
        linked = tmp_path / "contracts-link"
        _symlink_or_skip(linked, contracts)
        candidate = linked
    elif path_kind == "ancestor":
        linked = tmp_path / "parent-link"
        _symlink_or_skip(linked, tmp_path)
        candidate = linked / "contracts"
    else:
        original = contracts / path_kind
        moved = tmp_path / f"{path_kind}-target"
        original.rename(moved)
        _symlink_or_skip(original, moved)
        candidate = contracts
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            candidate,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_path_symlink"
    assert calls == []


def test_pilot_contracts_prepare_routes_to_shared_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "schema_version": "loveengine.contract-preparation/1",
        "prepared": True,
    }
    calls: list[bool] = []

    def fake_prepare(*, refresh_dependencies: bool) -> dict[str, object]:
        calls.append(refresh_dependencies)
        return expected

    monkeypatch.setattr(cli_pilot, "prepare_contracts", fake_prepare)

    args = build_parser().parse_args(["pilot", "contracts", "prepare"])

    assert dispatch(args) == expected
    refreshed = build_parser().parse_args(
        ["pilot", "contracts", "prepare", "--refresh-dependencies"]
    )
    assert dispatch(refreshed) == expected
    assert calls == [False, True]


def test_chain_initialization_requires_explicitly_prepared_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_: Path) -> dict[str, object]:
        raise LoveEngineError("contract_artifact_missing", "prepare first", 3)

    monkeypatch.setattr(pilot_chain, "verify_prepared_contract_artifacts", missing)

    with pytest.raises(LoveEngineError) as error:
        pilot_chain.initialize_chain(tmp_path, port=8545)

    assert error.value.code == "contract_artifact_missing"


def test_runtime_requires_artifacts_before_package_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> dict[str, object]:
        raise LoveEngineError("contract_artifact_missing", "prepare first", 3)

    monkeypatch.setattr(pilot_runtime, "verify_prepared_contract_artifacts", missing)
    monkeypatch.setattr(
        pilot_runtime,
        "build_package",
        lambda *args, **kwargs: pytest.fail("package build must not run"),
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_runtime.prepare_local_pilot_runtime(
            root=tmp_path,
            base_url="http://127.0.0.1:8780",
            host="127.0.0.1",
            port=8780,
            rpc_port=8545,
        )

    assert error.value.code == "contract_artifact_missing"
